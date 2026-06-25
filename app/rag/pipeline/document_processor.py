"""
文档处理器

职责：把原始文档切成小块（chunk），以及把检索到的小块还原为完整父文档。

核心概念：Small-to-Large 检索模式（小到大）

  写入阶段（现在）:
    原始菜谱文档（一整篇，如 5000 字）
      → MarkdownHeaderTextSplitter 按标题切分成多个 chunk（每块 200~500 字）
        → 每个 chunk 存入 Milvus，并保留 parent_id（指向原始文档）

  检索阶段（阶段4.4）:
    用户查询"番茄炒蛋怎么做"
      → Milvus 返回最相似的 chunk（是一小段文本）
        → 根据 chunk 的 parent_id 从 PostgreSQL 取出完整父文档
          → LLM 基于完整文档生成回答（而不是只看到一个碎片）

为什么不能直接用 chunk？
  - chunk 太小 → LLM 缺少上下文，回答不完整
  - chunk 太大 → 向量相似度匹配精度下降（噪音多）
  - Small-to-Large 兼顾两者：小 chunk 做精准匹配，大文档做完整回答
"""
import uuid
from typing import Any, Dict, List, Optional

from langchain_core.documents import Document
from langchain_text_splitters import MarkdownHeaderTextSplitter

import logging
logger = logging.getLogger(__name__)

# ===== 必需的元数据字段 =====
REQUIRED_METADATA_KEYS = (
    "source",         # 源文件路径
    "parent_id",      # 指向完整父文档的ID
    "dish_name",      # 菜品名称
    "category",       # 菜系分类
    "difficulty",     # 难度
    "is_dish_index",  # 是否是菜品索引页
    "data_source",    # 数据来源（howtocook / user_upload）
    "user_id",        # 用户 ID（个人文档用）
)


class DocumentProcessor:
    """
    统一文档处理器：
    - 切分文档为 chunk
    - 检索后将 chunk 还原为父文档
    """

    def __init__(self, headers_to_split_on: List[tuple] | None = None):
        """
        初始化文档处理器。

        参数：
            headers_to_split_on: Markdown 标题切分规则
                默认 [("#", "header_1"), ("##", "header_2")]
                表示遇到 # 一级标题或 ## 二级标题就切开
                例如 HowToCook 菜谱数据：
                  # 家常菜           ← 一级标题，切开
                  ## 番茄炒蛋        ← 二级标题，切开
                  番茄炒蛋的做法...
                  ## 麻婆豆腐        ← 二级标题，切开
                  麻婆豆腐的做法...
        """
        self.headers_to_split_on = headers_to_split_on or [
            ("#", "header_1"),
            ("##", "header_2"),
        ]
        # MarkdownHeaderTextSplitter 是 LangChain 提供的文本分割器
        # strip_headers=False 表示保留标题文本在 chunk 中（不丢掉）
        self._splitter = MarkdownHeaderTextSplitter(
            headers_to_split_on=self.headers_to_split_on,
            strip_headers=False,
        )

    def create_chunks(
        self,
        doc_id: str,
        content: str,
        metadata: Dict[str, Any],
    ) -> List[Document]:
        """
        把一篇完整文档切分成多个 chunk。

        参数：
            doc_id: 父文档唯一 ID（后续通过 parent_id 找回完整文档）
            content: 文档全文（Markdown 格式）
            metadata: 基础元数据（会被复制到每个 chunk 上）

        返回：
            Document 列表，每个 Document 是一个 chunk

        示例：
            输入：doc_id="abc", content="# 家常菜\n## 番茄炒蛋\n...\n## 麻婆豆腐\n..."
            输出：[
                Document(content="# 家常菜\n## 番茄炒蛋\n...", metadata={"parent_id": "abc", ...}),
                Document(content="## 麻婆豆腐\n...", metadata={"parent_id": "abc", ...}),
            ]
        """
        chunks: List[Document] = []
        # split_text(content) 按标题把 content 切成多段
        # 每段是一个 Document，metadata 中包含 Header 信息
        md_chunks = self._splitter.split_text(content)
        for chunk_doc in md_chunks:
            # 复制原始元数据，并注入 parent_id（这是关键！）
            chunk_metadata = self._clone_metadata(metadata, parent_id=doc_id)
            chunks.append(Document(
                id=str(uuid.uuid4()),            # chunk 自己的唯一 ID
                page_content=chunk_doc.page_content,  # chunk 的文本内容
                metadata=chunk_metadata,         # 包含 parent_id 的元数据
            ))
        return chunks

    async def post_process_retrieval(
        self,
        retrieved_chunks: List[Document],
    ) -> List[Document]:
        """
        检索后处理：把检索到的小 chunk 替换为完整的父文档。

        这是 Small-to-Large 的核心步骤：
        1. 收集所有 chunk 的 parent_id
        2. 从 PostgreSQL 取出对应的完整父文档
        3. 去重（多个 chunk 可能属于同一个父文档）
        4. 保留每个父文档的最高检索分数
        """
        if not retrieved_chunks:
            return []

        # ===== 第1步：按 parent_id 分组，保留每个 parent 的最高分数 =====
        parent_scores: Dict[str, float] = {}
        for chunk in retrieved_chunks:
            parent_id = chunk.metadata.get("parent_id")
            if not parent_id:
                continue
            retrieval_score = chunk.metadata.get("retrieval_score", 0.0)
            if parent_id not in parent_scores:
                parent_scores[parent_id] = retrieval_score
            else:
                if retrieval_score > parent_scores[parent_id]:
                    parent_scores[parent_id] = retrieval_score

        if not parent_scores:
            return retrieved_chunks

        parent_ids = list(parent_scores.keys())

        # ===== 第2步：从 PostgreSQL 批量取出完整父文档 =====
        from app.database.session import AsyncSessionLocal
        from app.ingestion.document_repo import RecipeDocumentRepository

        try:
            async with AsyncSessionLocal() as db:
                doc_repo = RecipeDocumentRepository(db)
                parent_docs = await doc_repo.get_by_ids(parent_ids)
        except Exception as e:
            logger.error("Failed to fetch parent documents: %s", e)
            # 数据库挂了，降级返回 chunk
            for chunk in retrieved_chunks:
                pid = chunk.metadata.get("parent_id")
                if pid in parent_scores:
                    chunk.metadata["retrieval_score"] = parent_scores[pid]
            retrieved_chunks.sort(
                key=lambda d: d.metadata.get("retrieval_score", 0.0),
                reverse=True,
            )
            return retrieved_chunks

        # ===== 第3步：组装最终文档列表并去重 =====
        parent_map = {doc.id: doc for doc in parent_docs}
        final_docs: List[Document] = []
        seen_ids: set = set()

        for parent_id in parent_ids:
            if parent_id in seen_ids:
                continue
            seen_ids.add(parent_id)

            parent_doc = parent_map.get(parent_id)
            if parent_doc:
                final_docs.append(Document(
                    page_content=parent_doc.content,
                    metadata={
                        "parent_id": parent_id,
                        "dish_name": parent_doc.dish_name,
                        "category": parent_doc.category,
                        "difficulty": parent_doc.difficulty,
                        "source": parent_doc.source,
                        "data_source": parent_doc.data_source,
                        "retrieval_score": parent_scores[parent_id],
                        "is_full_document": True,
                    },
                ))
            else:
                # 父文档被删了，用分数最高的 chunk 作为降级
                best_chunk = max(
                    (c for c in retrieved_chunks if c.metadata.get("parent_id") == parent_id),
                    key=lambda c: c.metadata.get("retrieval_score", 0.0),
                    default=None,
                )
                if best_chunk:
                    best_chunk.metadata["retrieval_score"] = parent_scores[parent_id]
                    final_docs.append(best_chunk)

        # 按分数排序
        final_docs.sort(
            key=lambda d: d.metadata.get("retrieval_score", 0.0),
            reverse=True,
        )

        logger.info(
            "Small-to-Large: %d chunks → %d unique docs (%d from PG)",
            len(retrieved_chunks), len(final_docs),
            sum(1 for d in final_docs if d.metadata.get("is_full_document")),
        )
        return final_docs
    
          # ===== 内部工具方法 =====

    def _clone_metadata(
          self,
          metadata: Dict[str, Any],
          *,
          parent_id: Optional[str] = None,
      ) -> Dict[str, Any]:
          """
          复制元数据字典，只保留 REQUIRED_METADATA_KEYS 中的字段。
          如果指定了 parent_id，覆盖原来的值。

          这是在创造 chunk 时的关键操作——确保每个 chunk 都知道自己
          "来自哪个文档（parent_id）"，后续检索时才能找回完整父文档。
          """
          cloned = {key: metadata.get(key, "") for key in REQUIRED_METADATA_KEYS}
          if parent_id is not None:
              cloned["parent_id"] = parent_id
          return cloned
    

        
#   模块解释

#   Small-to-Large 的完整流程图示：

#   === 数据写入阶段（现在定义 create_chunks）===

#   原始菜谱文档 (parent):
#   ┌─────────────────────────────────────────┐
#   │ id: "doc_001"                           │
#   │ # 家常菜                                 │
#   │ ## 番茄炒蛋                              │
#   │ 番茄炒蛋的做法：1. 准备番茄 2. 打鸡蛋...  │
#   │ ## 麻婆豆腐                              │
#   │ 麻婆豆腐的做法：1. 准备豆腐 2. 炒肉末...  │
#   └─────────────────────────────────────────┘
#              ↓ MarkdownHeaderTextSplitter 切分

#   chunk_001: { content="番茄炒蛋的做法...", metadata={ parent_id:
#   "doc_001" } }
#   chunk_002: { content="麻婆豆腐的做法...", metadata={ parent_id:
#   "doc_001" } }
#              ↓ 存入 Milvus（每个 chunk 生成 dense + sparse 向量）


#   === 检索阶段（阶段4.4-4.5）===

#   用户查询："番茄炒蛋"
#              ↓ Milvus 向量相似度搜索
#   命中 chunk_001（分数 0.92）
#              ↓ post_process_retrieval()
#   根据 parent_id="doc_001" 从 PostgreSQL 取出完整文档（包含麻婆豆腐部分）
#              ↓ LLM 看到完整上下文，生成更准确的回答

#   为什么这是 RAG 质量的关键？

#   假设没有 Small-to-Large，直接检索到大 chunk：
#   - chunk 太大 → 向量稀释（"番茄炒蛋"的信息被"麻婆豆腐"的内容稀释了）
#   - 检索精度下降 → 不应该被检索到的文档被错误地排到前面

#   假设没有 post_process_retrieval，直接用小 chunk 喂 LLM：
#   - LLM 只看到碎片 → "番茄炒蛋的做法：1. 准备番茄 2. 打鸡蛋"后面没了
#   - 回答不完整 → 用户要再问一遍"然后呢？"
