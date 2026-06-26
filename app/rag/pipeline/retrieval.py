"""
混合检索模块

职责：在 Milvus 中执行 Dense（语义）+ Sparse（关键词）双路混合检索。

为什么需要混合检索？
  纯 Dense 向量检索（BGE Embedding）的问题：
    - "番茄炒蛋" 和 "番茄蛋花汤" 语义相似，但用户要的是炒蛋不是汤
    - Dense 只关注语义，忽略了精确关键词匹配
  纯 Sparse 关键词检索（BM25）的问题：
    - 用户搜"怎么做西红柿炒鸡蛋"，菜谱标题是"番茄炒蛋做法"
    - BM25 不认识"西红柿=番茄"，无法匹配同义词

  混合检索 = Dense + Sparse 加权融合
    - Dense 捕获同义词、概念相似度 → "番茄" ≈ "西红柿"
    - Sparse 捕获精确关键词、术语匹配 → "炒蛋" ≠ "蛋汤"
    - 两者互补，检索精度远高于单一方法

智能 ranker 选择：
  查询"番茄炒蛋怎么做" → 关键词型查询 → BM25 权重 ↑
  查询"适合夏天的清淡菜" → 语义型查询 → Dense 权重 ↑
"""
import asyncio
import logging
from typing import List, Optional, Tuple

from langchain_core.documents import Document
from langchain_milvus import Milvus

logger = logging.getLogger(__name__)


class RetrievalOptimizationModule:
    """
    Milvus 混合检索封装。

    核心方法：
        hybrid_search() — 执行混合检索
        intelligent_ranker_selection() — 根据查询特征自动选择融合权重
    """

    def __init__(
        self,
        vectorstore: Milvus,
        score_threshold: float = 0.0,
        default_ranker_type: str = "weighted",
        default_ranker_weights: Optional[List[float]] = None,
    ):
        """
        初始化检索模块。

        参数：
            vectorstore: 已连接的 Milvus 实例
            score_threshold: 最低相关性分数（低于此分数的结果丢弃）
            default_ranker_type: 默认融合方式
                "weighted" → 加权求和：final_score = w1*dense + w2*sparse
                "rrf"      → 倒数排名融合（Reciprocal Rank Fusion）
            default_ranker_weights: 默认权重 [dense_weight, sparse_weight]
        """
        if not vectorstore:
            raise ValueError("Vectorstore must be provided.")

        self.vectorstore = vectorstore
        self.score_threshold = score_threshold
        self.default_ranker_type = default_ranker_type
        self.default_ranker_weights = default_ranker_weights or [0.8, 0.2]

    async def hybrid_search(
        self,
        query: str,
        top_k: int,
        ranker_type: Optional[str] = None,
        ranker_weights: Optional[List[float]] = None,
        score_threshold: Optional[float] = None,
        expr: Optional[str] = None,
    ) -> Tuple[List[Document], List[float]]:
        """
        执行混合检索。

        参数：
            query: 用户查询文本
            top_k: 返回的最大文档数
            ranker_type: "weighted" 或 "rrf"
            ranker_weights: [dense_weight, sparse_weight]
            score_threshold: 最低分数阈值
            expr: Milvus 过滤表达式，如 'user_id == "u123"'
                  用于只查某个用户的个人文档

        返回：
            (documents, scores) — 文档列表和对应的分数列表，一一对应

        内部流程：
            1. Milvus 同时计算 Dense 相似度 + BM25 分数
            2. 按 ranker_type 融合两个分数
            3. 返回 top_k 个最相关的文档
        """
        # 使用默认值
        if ranker_type is None:
            ranker_type = self.default_ranker_type
        if ranker_weights is None:
            ranker_weights = self.default_ranker_weights
        if score_threshold is None:
            score_threshold = self.score_threshold

        # 构建 ranker 参数
        # norm_score=True：把两个分数归一化到同一尺度再融合
        # 否则 Dense 分数范围 [0.7~1.0] 和 BM25 分数范围 [0~50] 直接加权没有意义
        ranker_params = {"norm_score": True}
        if ranker_type == "weighted":
            ranker_params["weights"] = ranker_weights

        logger.info(
            "Hybrid search: query='%s...' top_k=%d ranker=%s weights=%s expr=%s",
            query[:50],
            top_k,
            ranker_type,
            ranker_weights,
            expr,
        )

        # ===== 执行 Milvus 相似度搜索 =====
        # similarity_search_with_score 是 LangChain Milvus 封装的方法
        # 它调用 Milvus 的 hybrid_search API，自动处理 Dense + Sparse 双路
        #
        # fetch_k = top_k * 4：
        #   先取 4 倍的候选集，融合后再截断 top_k
        #   防止融合过程中丢失高质量候选
        #
        # asyncio.to_thread：
        #   LangChain 的 similarity_search_with_score 是同步的
        #   用 asyncio.to_thread 把它放到线程池执行，避免阻塞事件循环
        results = await asyncio.to_thread(
            self.vectorstore.similarity_search_with_score,
            query=query,
            k=top_k,
            fetch_k=int(top_k * 4),
            ranker_type=ranker_type,
            ranker_params=ranker_params,
            expr=expr,
        )

        # 分离文档和分数
        docs, scores = [], []
        for doc, score in results:
            docs.append(doc)
            scores.append(score)

        logger.info("Hybrid search returned %d docs", len(docs))

        # ===== 分数阈值过滤 =====
        # 注意：只有 weighted 模式下分数才有统一尺度（0~1）
        # rrf 模式下分数含义不同，不做阈值过滤
        if score_threshold > 0 and ranker_type == "weighted":
            filtered_docs, filtered_scores = [], []
            for doc, score in zip(docs, scores):
                if score >= score_threshold:
                    filtered_docs.append(doc)
                    filtered_scores.append(score)

            logger.info(
                "Score filter: %d → %d (threshold=%s)",
                len(docs), len(filtered_docs), score_threshold,
            )
            return filtered_docs, filtered_scores

        return docs, scores

    def intelligent_ranker_selection(self, query: str) -> Tuple[str, List[float]]:
        """
        根据查询文本的特征，自动选择最优的融合权重。

        判断逻辑：
        - 含"怎么做/如何/步骤/方法" → 用户在做菜，关键词更重要 → BM25 权重 ↑
        - 含"推荐/有什么/哪些/适合" → 用户在选择，语义更重要 → Dense 权重 ↑
        - 其他 → 均衡权重

        这是一种基于规则的方法，可以后续替换为 ML 模型。

        参数：
            query: 用户查询文本

        返回：
            (ranker_type, weights)
        """
        query_lower = query.lower()

        # 关键词型查询：用户要找精确的操作指南
        keyword_indicators = [
            "怎么做", "如何", "步骤", "方法", "做法", "recipe", "how to",
        ]
        if any(indicator in query_lower for indicator in keyword_indicators):
            logger.debug("Query type: keyword-heavy, BM25 bias")
            return "weighted", [0.4, 0.6]  # BM25(sparse) 权重 0.6

        # 语义型查询：用户在寻找推荐、建议
        semantic_indicators = [
            "推荐", "类似", "什么菜", "有哪些", "有什么",
            "适合", "建议", "recommend", "similar", "suggest",
        ]
        if any(indicator in query_lower for indicator in semantic_indicators):
            logger.debug("Query type: semantic-heavy, Dense bias")
            return "weighted", [0.6, 0.4]  # Dense 权重 0.6

        # 均衡型
        return "weighted", [0.5, 0.5]

    @staticmethod
    def build_metadata_filter_expr(
        difficulty: int | None = None,
        category: str | None = None,
        cooking_time_minutes: int | None = None,
        tags: list | None = None,
    ) -> str | None:
        """
        构建 Milvus 标量过滤表达式。

        参数来自 IntentDetector 提取的 filters。

        示例：
          difficulty=2, category="soup"
          → 'difficulty <= 2 and category == "soup"'

        为什么在 Milvus 侧过滤而不是 Python 侧？
          - Milvus 支持标量字段索引过滤，性能远高于 Python 后过滤
          - 先过滤再检索保证返回 top_k 个全部符合条件
          - 如果先取 top_k 再 Python 过滤，可能符合条件的只剩 2 条
        """
        parts = []

        if difficulty is not None:
            parts.append(f"difficulty <= {int(difficulty)}")

        if category is not None and category.strip():
            parts.append(f'category == "{category}"')

        if cooking_time_minutes is not None:
            parts.append(f"cooking_time_minutes <= {int(cooking_time_minutes)}")

        if tags:
            for tag in tags:
                parts.append(f'array_contains(tags, "{tag}")')

        return " and ".join(parts) if parts else None


# ===== 模块解释 =====
#
# 混合检索的数学原理（Weighted 模式）：
#
# 查询: "番茄炒蛋怎么做"
#
# Milvus 内部同时计算:
#   Dense 分数:  [文档A: 0.89, 文档B: 0.72, 文档C: 0.45, ...]
#   Sparse 分数: [文档A: 0.55, 文档B: 0.91, 文档C: 0.23, ...]
#
# norm_score=True 后（归一化到 0~1）:
#   Dense:  [A: 0.89, B: 0.72, C: 0.45]
#   Sparse: [A: 0.55, B: 0.91, C: 0.23]
#
# Weighted 融合 (dense=0.4, sparse=0.6):
#   A: 0.4×0.89 + 0.6×0.55 = 0.356 + 0.330 = 0.686
#   B: 0.4×0.72 + 0.6×0.91 = 0.288 + 0.546 = 0.834  ← B 胜出！
#   C: 0.4×0.45 + 0.6×0.23 = 0.180 + 0.138 = 0.318
#
# 如果只用 Dense (权重 [1.0, 0.0]):
#   A: 0.89 → 排名第1（但 A 可能关键词匹配不准！）
#
# 如果只用 Sparse (权重 [0.0, 1.0]):
#   B: 0.91 → 排名第1（但 B 可能语义不相关！）
#
# 混合检索取两者之长，B=0.834 > A=0.686，返回了更匹配的结果。
#
# 智能 ranker 选择的实际效果：
#
# 查询"番茄炒蛋怎么做"
#   → 检测到"怎么做" → keyword 型 → BM25 权重 0.6
#   → 优先匹配标题/步骤中含"番茄炒蛋"的文档（精确）
#   → 用户得到精确的操作步骤
#
# 查询"有什么适合夏天的清淡菜"
#   → 检测到"有什么/适合" → semantic 型 → Dense 权重 0.6
#   → 优先匹配"凉拌""蒸""时蔬""清淡"等语义相近的菜谱
#   → 用户得到符合场景的推荐列表
