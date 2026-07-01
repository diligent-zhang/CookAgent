"""
RAG 服务

职责：把分散的 RAG 管道组件（Embedding、向量库、检索、缓存、后处理）
     组装成统一的检索接口，供 Conversation Service 调用。

完整的检索流程：
  用户查询 "番茄炒蛋怎么做"
    → CacheManager.get()          检查缓存
      → 命中？ 直接返回
      → 未命中？ 继续
    → get_embedding_model()       把查询转为 512 维向量
    → RetrievalModule.hybrid_search()  Milvus 混合检索 (Dense + Sparse)
    → DocumentProcessor.post_process() Small-to-Large 父文档还原 + 去重
    → CacheManager.set()          写入缓存
    → 返回文档列表
"""
import logging
from typing import List, Optional

from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings
from langchain_milvus import Milvus

from app.config import settings
from app.rag.cache.cache_manager import CacheManager
from app.rag.embeddings.embedding_factory import get_embedding_model
from app.rag.pipeline.document_processor import DocumentProcessor
from app.rag.pipeline.retrieval import RetrievalOptimizationModule
from app.rag.vector_stores.vector_store_factory import get_vector_store

logger = logging.getLogger(__name__)


class RAGService:
    """
    RAG 检索服务

    用法：
        rag = RAGService(redis_client)
        await rag.initialize()           # 连接 Milvus、加载 Embedding 模型
        docs = await rag.retrieve("番茄炒蛋怎么做", user_id="u123")
    """

    def __init__(self, redis_client=None):
        self.redis_client = redis_client

        # 这些在 initialize() 中赋值
        self.embeddings: Optional[Embeddings] = None
        self.recipe_store: Optional[Milvus] = None
        self.cache_store: Optional[Milvus] = None
        self.retrieval: Optional[RetrievalOptimizationModule] = None
        self.processor: Optional[DocumentProcessor] = None
        self.cache: Optional[CacheManager] = None
        self._initialized = False

        # Reranker（精排器），由外部通过 set_reranker() 注入
        # 可选组件——配置关了或初始化失败时为 None，检索时自动跳过
        self.reranker = None

    async def initialize(self) -> None:
        """
        初始化 RAG 管道的所有组件。

        分四步：
        1. 加载 Embedding 模型
        2. 连接 Milvus 菜谱 Collection
        3. 创建检索模块 + 文档处理器
        4. 初始化双层缓存
        """
        if self._initialized:
            return

        logger.info("Initializing RAG service...")

        # 1. 加载本地 BGE Embedding 模型（首次加载会从 HuggingFace 下载 ~100MB）
        self.embeddings = get_embedding_model(settings.embedding.model_name)

        # 2. 连接菜谱向量库（cook_hero_recipes collection）
        #    注意：chunks=[] 意味着不创建新 collection，只连接已存在的
        self.recipe_store = get_vector_store(
            collection_name=settings.vector_store.collection_names["recipes"],
            embeddings=self.embeddings,
            chunks=[],  # 空列表 → 只连接，不创建
        )

        # 3. 创建检索模块（混合检索）和文档处理器（Small-to-Large）
        self.retrieval = RetrievalOptimizationModule(
            vectorstore=self.recipe_store,
            score_threshold=settings.retrieval.score_threshold,
            default_ranker_type=settings.retrieval.ranker_type,
            default_ranker_weights=settings.retrieval.ranker_weights,
        )
        self.processor = DocumentProcessor()

        # 4. 初始化 L2 语义缓存（如果启用）
        if settings.cache.l2_enabled:
            self.cache_store = get_vector_store(
                collection_name=settings.cache.vector_collection,
                embeddings=self.embeddings,
                chunks=[],
            )
        else:
            self.cache_store = None

        self.cache = CacheManager(
            redis_client=self.redis_client,
            vectorstore=self.cache_store,
            embeddings=self.embeddings,
            ttl=settings.cache.ttl,
            l2_enabled=settings.cache.l2_enabled,
            similarity_threshold=settings.cache.similarity_threshold,
        )

        self._initialized = True
        logger.info("RAG service initialized successfully")

    def set_reranker(self, reranker):
        """
        注入精排器（DashScopeReranker）。

        在 initialize() 之后由 init_agent_module() 调用。
        Reranker 是可选组件——为 None 时 retrieve() 自动跳过精排步骤。

        为什么用 setter 而不是 __init__ 参数？
          Reranker 依赖 settings.reranker_* 配置，这些配置和 LLM/Embedding
          配置分离。保持 RAGService 的构造简洁，通过 setter 做可选注入。
        """
        self.reranker = reranker

    async def retrieve(
        self,
        query: str,
        user_id: str = "",
        top_k: Optional[int] = None,
        expr: Optional[str] = None,
    ) -> List[Document]:
        """
        执行完整的 RAG 检索。

        参数：
            query: 用户的自然语言查询
            user_id: 用户 ID（用于缓存键和个性化过滤）
            top_k: 返回的文档数量上限
            expr: Milvus 过滤表达式（如按菜系过滤）

        返回：
            去重后的完整父文档列表（按相关性从高到低）
        """
        if not self._initialized:
            await self.initialize()

        if top_k is None:
            top_k = settings.retrieval.top_k

        # ==== L1 + L2 缓存检查 ====
        cached_docs = await self.cache.get(query, user_id)
        if cached_docs is not None:
            return cached_docs[:top_k]

        # ==== 智能 ranker 选择 ====
        ranker_type, ranker_weights = self.retrieval.intelligent_ranker_selection(query)

        # ==== Milvus 混合检索 ====
        retrieved_chunks, scores = await self.retrieval.hybrid_search(
            query=query,
            top_k=top_k,
            ranker_type=ranker_type,
            ranker_weights=ranker_weights,
            expr=expr,
        )

        if not retrieved_chunks:
            logger.info("RAG retrieve: no results for '%s...'", query[:40])
            return []

        # 把相关性分数注入到 chunk 的 metadata 中
        for doc, score in zip(retrieved_chunks, scores):
            doc.metadata["retrieval_score"] = float(score)

        # ==== Small-to-Large：chunk → 父文档 → 去重 ====
        final_docs = await self.processor.post_process_retrieval(retrieved_chunks)

        # ==== 精排（P1 修复：Reranker 正式接入检索管道）====
        # 混合检索（Dense+Sparse）是"粗排"——基于向量距离和 BM25 打分，
        # 关注的是"长得像"。Reranker 是"精排"——用 Cross-Encoder 做深度
        # 语义匹配，关注的是"真正相关"。
        #
        # 典型效果：粗排 top 20 中混入了"番茄蛋花汤"（和"番茄炒蛋"向量相似），
        #          Reranker 能识别出这其实不相关并把它排到后面。
        #
        # 降级策略：reranker 为 None（未配置或初始化失败）→ 跳过精排
        #           reranker.rerank() 抛异常 → 打 warning，用粗排结果
        if self.reranker and final_docs:
            try:
                final_docs = await self.reranker.rerank(
                    query=query,
                    documents=final_docs,
                    top_n=top_k,
                )
                logger.info(
                    "Reranker: %d docs re-ranked for query='%s...'",
                    len(final_docs), query[:40],
                )
            except Exception as e:
                logger.warning("Reranker failed, using coarse ranking: %s", e)
                # 精排失败不阻塞主流程，降级用粗排结果

        # ==== 写入缓存 ====
        # 注意：缓存的是精排后的结果，下次相同查询直接命中缓存，不再重复精排
        await self.cache.set(query, final_docs, user_id)

        logger.info(
            "RAG retrieve: query='%s...' → %d chunks → %d final docs",
            query[:40], len(retrieved_chunks), len(final_docs),
        )
        return final_docs[:top_k]


# ===== 架构图 =====
#
#   用户查询
#      │
#      ▼
#   CacheManager.get()
#      ├─ L1 Redis: 精确匹配 → 命中? 直接返回
#      └─ L2 Milvus: 语义匹配 → 命中? 直接返回
#      │ (未命中)
#      ▼
#   RetrievalOptimizationModule.hybrid_search()
#      ├─ Dense 向量检索 (BGE 语义)
#      ├─ Sparse 向量检索 (BM25 关键词)
#      └─ Weighted 融合 → 返回 top_k chunks
#      │
#      ▼
#   DocumentProcessor.post_process_retrieval()
#      ├─ 收集 parent_id → 从 PG 取完整父文档
#      └─ 去重 + 排序 → 返回完整文档列表
#      │
#      ▼
#   CacheManager.set()
#      └─ 写入 L1 + L2 缓存
#
# 为什么 initialize() 和 retrieve() 分开：
#   - 模型加载（~100MB BGE 模型）和 Milvus 连接是慢操作
#   - FastAPI 的 lifespan 事件中调用 initialize()，请求到来时直接 retrieve()
#   - 避免每个请求都重新加载模型
