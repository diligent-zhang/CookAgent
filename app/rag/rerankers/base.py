"""
重排序器统一接口

定义重排序器的抽象基类。

为什么需要 Reranker？Milvus 混合检索是"粗排"——基于向量距离和 BM25 打分，
关注"长得像"。Reranker 是"精排"——用 Cross-Encoder 深度语义匹配，关注"真正相关"。
例如用户搜"适合老人的菜"，粗排会返回"老人头菌炒肉"（字面匹配"老人"），精排
会把它降权，提升"软烂易消化"的菜谱。
"""
from abc import ABC, abstractmethod
from typing import List

from langchain_core.documents import Document


class BaseReranker(ABC):
    """重排序器基类。"""

    @abstractmethod
    async def rerank(
        self,
        query: str,
        documents: List[Document],
        top_n: int = 5,
    ) -> List[Document]:
        """
        重排序文档列表。

        参数：
            query: 用户查询
            documents: 候选文档列表（从 Milvus 检索的 top_k=9）
            top_n: 返回的文档数量（精排后保留 5 个）

        返回：
            按相关性重新排序的文档列表，每个文档的 metadata 会新增 rerank_score 字段
        """
        ...
