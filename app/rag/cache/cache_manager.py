"""
双层缓存管理器

为什么需要缓存？
  RAG 检索的延迟 = Embedding 编码(50ms) + Milvus 向量搜索(10ms) + 后处理(10ms)
  如果多个用户反复问同一个问题（如"番茄炒蛋怎么做"），每次都重复检索是浪费

双层缓存策略：
  L1 (Redis):  精确匹配 → 查询文本完全相同 → 直接返回缓存结果
               延迟：~1ms（内存级别）
  L2 (Milvus): 语义匹配 → 查询文本语义相似度 ≥ 0.92 → 返回缓存结果
               延迟：~60ms（还需要跑一遍 Embedding + 向量搜索，但数据量小得多）

为什么分两层？
  L1 命中的概率低（需要完全相同的查询），但速度极快
  L2 命中的概率高（相似查询都能命中），但比 L1 慢
  两层互补：大部分常见问题走 L2，完全相同的问题走 L1

缓存什么？
  只缓存"查询 → 检索到的文档列表"，不缓存 LLM 生成的回答
  因为 LLM 回答会变化（模型升级、温度参数、用户画像不同），但检索结果是确定的
"""
import asyncio
import hashlib
import json
import logging
from typing import List, Optional

from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings

logger = logging.getLogger(__name__)


class CacheManager:
    """
    双层缓存管理器
    L1：Redis（精确匹配）
    L2：Milvus 缓存 Collection（语义匹配）
    """

    def __init__(
        self,
        redis_client=None,
        vectorstore=None,             # 专用的 L2 缓存 Milvus 实例
        embeddings: Embeddings | None = None,
        ttl: int = 3600,              # 缓存过期时间（秒）
        l2_enabled: bool = True,
        similarity_threshold: float = 0.92,  # L2 语义命中阈值
    ):
        """
        初始化缓存管理器。

        参数：
            redis_client: Redis 客户端（用于 L1 精确匹配缓存）
            vectorstore:  Milvus 实例（用于 L2 语义缓存，通常是一个专用的 cache collection）
            embeddings:  Embedding 模型（L2 检索时需要把 query 转为向量）
            ttl:         缓存过期时间，默认 3600 秒（1 小时）
            l2_enabled:  是否启用 L2 语义缓存
            similarity_threshold: L2 语义相似度阈值，≥0.92 才认为命中
        """
        self.redis_client = redis_client
        self.vectorstore = vectorstore
        self.embeddings = embeddings
        self.ttl = ttl
        self.l2_enabled = l2_enabled
        self.similarity_threshold = similarity_threshold

    # ===== 缓存查询 =====

    async def get(self, query: str, user_id: str = "") -> Optional[List[Document]]:
        """
        尝试从缓存获取检索结果。

        查询顺序：L1 → L2 → 未命中

        参数：
            query: 用户的原始查询文本
            user_id: 用户 ID（个人文档检索时用于区分用户）

        返回：
            命中的文档列表（反序列化后的 LangChain Document 对象）
            未命中返回 None
        """
        # ---- L1: Redis 精确匹配 ----
        cache_key = self._build_cache_key(query, user_id)

        if self.redis_client:
            try:
                cached = await self.redis_client.get(cache_key)
                if cached:
                    logger.info("Cache L1 hit: '%s...'", query[:40])
                    return self._deserialize_docs(json.loads(cached))
            except Exception as e:
                logger.warning("Redis L1 get failed: %s", e)

        # ---- L2: Milvus 语义缓存 ----
        if self.l2_enabled and self.vectorstore and self.embeddings:
            try:
                # 在缓存 Collection 中搜索最相似的查询
                results = await asyncio.to_thread(
                    self.vectorstore.similarity_search_with_score,
                    query=query,
                    k=1,  # 只要最相似的一条
                )

                if results:
                    doc, score = results[0]
                    if score >= self.similarity_threshold:
                        logger.info(
                            "Cache L2 hit: '%s...' (score=%.3f)",
                            query[:40], score,
                        )
                        # L2 缓存 doc 的 metadata 中存储了上次检索结果的序列化数据
                        cached_data = doc.metadata.get("cached_results")
                        if cached_data:
                            return self._deserialize_docs(json.loads(cached_data))
            except Exception as e:
                logger.warning("Milvus L2 get failed: %s", e)

        logger.debug("Cache miss: '%s...'", query[:40])
        return None

    # ===== 缓存写入 =====

    async def set(
        self,
        query: str,
        documents: List[Document],
        user_id: str = "",
    ) -> None:
        """
        将检索结果写入双层缓存。

        参数：
            query: 用户的查询文本
            documents: 检索到的文档列表
            user_id: 用户 ID
        """
        serialized = json.dumps(
            [self._serialize_doc(doc) for doc in documents],
            ensure_ascii=False,
        )

        # ---- L1: Redis 精确匹配 ----
        if self.redis_client:
            try:
                cache_key = self._build_cache_key(query, user_id)
                await self.redis_client.setex(cache_key, self.ttl, serialized)
                logger.debug("Cache L1 set: '%s...'", query[:40])
            except Exception as e:
                logger.warning("Redis L1 set failed: %s", e)

        # ---- L2: Milvus 语义缓存 ----
        if self.l2_enabled and self.vectorstore and self.embeddings:
            try:
                # 把查询文本和检索结果打包成一个 Document 存入 L2 缓存 Collection
                cache_doc = Document(
                    page_content=query,  # 查询文本作为向量化的内容
                    metadata={
                        "cached_results": serialized,  # 检索结果序列化存这里
                        "user_id": user_id,
                    },
                )

                await asyncio.to_thread(
                    self.vectorstore.add_documents,
                    documents=[cache_doc],
                )
                logger.debug("Cache L2 set: '%s...'", query[:40])
            except Exception as e:
                logger.warning("Milvus L2 set failed: %s", e)

    # ===== 缓存失效 =====

    async def invalidate(self, user_id: str = "") -> None:
        """
        清除指定用户的缓存。

        当用户上传/删除了个人文档时调用，因为文档变了，旧的缓存结果不再有效。
        """
        pattern = f"cookhero:cache:{user_id}:*"
        deleted_count = 0

        # L1: Redis SCAN + DELETE 按模式匹配删除
        if self.redis_client:
            try:
                cursor = 0
                while True:
                    cursor, keys = await self.redis_client.scan(
                        cursor, match=pattern, count=100
                    )
                    if keys:
                        await self.redis_client.delete(*keys)
                        deleted_count += len(keys)
                    if cursor == 0:
                        break
                logger.info("Cache L1 invalidated: %d keys for user_id=%s", deleted_count, user_id)
            except Exception as e:
                logger.warning("Redis L1 invalidate failed: %s", e)

        # L2: Milvus 按 user_id 删除缓存文档
        if self.l2_enabled and self.vectorstore:
            try:
                result = self.vectorstore.delete(
                    expr=f'user_id == "{user_id}"'
                )
                logger.info("Cache L2 invalidated for user_id=%s: %s", user_id, result)
            except Exception as e:
                logger.warning("Milvus L2 invalidate failed: %s", e)

    # ===== 内部工具方法 =====

    def _build_cache_key(self, query: str, user_id: str) -> str:
        """
        构建 L1 缓存键。

        键格式：cookhero:cache:{user_id}:{query_md5}

        {user_id} 的作用：
          不同用户的个人文档不同，同一个查询"减脂餐推荐"
          用户 A（有过敏食物）和用户 B 的检索结果应该不同
          所以 user_id 是缓存键的一部分
        """
        query_hash = hashlib.md5(query.encode("utf-8")).hexdigest()
        return f"cookhero:cache:{user_id}:{query_hash}"

    def _serialize_doc(self, doc: Document) -> dict:
        """把 LangChain Document 序列化为 dict（JSON 可存）。"""
        return {
            "page_content": doc.page_content,
            "metadata": doc.metadata,
        }

    def _deserialize_docs(self, data: list) -> List[Document]:
        """从 dict 列表反序列化为 LangChain Document 列表。"""
        return [
            Document(
                page_content=item["page_content"],
                metadata=item.get("metadata", {}),
            )
            for item in data
        ]


# ===== 模块解释 =====
#
# 双层缓存的决策树：
#
# 用户查询 "番茄炒蛋怎么做"
#         │
#         ▼
#    ┌─ L1: Redis 精确匹配 ─┐
#    │ Key: cookhero:cache:  │
#    │  <user_id>:<md5>      │
#    │ 这串 MD5 存在吗？      │
#    └───────┬──────────────┘
#            │
#       ┌────┴────┐
#      存在        不存在
#       │           │
#       ▼           ▼
#   直接返回    ┌─ L2: Milvus 语义缓存 ─┐
#   (1ms)      │ 把 query 转为向量      │
#              │ 在 cache collection   │
#              │ 中搜索相似查询         │
#              │ 相似度 ≥ 0.92？        │
#              └───────┬──────────────┘
#                      │
#                 ┌────┴────┐
#                命中       未命中
#                 │           │
#                 ▼           ▼
#             返回缓存结果   执行完整 RAG 检索
#             (60ms)        (200ms+)
#                              │
#                              ▼
#                         结果写入 L1+L2 缓存
#                         （下次相同/相似查询直接命中）
#
# 为什么 L2 存的是"查询文本"而不是"向量"？
#
# L2 缓存也是一个 Milvus Collection，存入的是用户的查询文本 + 缓存结果。
# 当新查询到来时，把新查询转为向量，在 L2 缓存中搜索之前存过的相似查询，
# 如果找到了（相似度≥0.92），说明"这个问题之前有人问过几乎一样的"，
# 直接返回之前缓存的结果。
#
# 本质上，L2 是一个查询去重器——利用向量相似度判断"这个新问题是不是之前已经回答过了"。
