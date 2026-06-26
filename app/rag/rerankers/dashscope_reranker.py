"""
DashScope Reranker

使用阿里云 DashScope 的 gte-rerank 模型进行检索结果精排。
走 HTTP API 调用，和现有 LLM 用同一个 API Key。

降级策略：
  - API 超时 → 返回粗排结果的前 top_n 个
  - API 返回格式异常 → 返回粗排结果的前 top_n 个
  - 不阻断主流程，降级总比报错好
"""
import logging
from typing import List

import httpx
from langchain_core.documents import Document
from app.config import settings
from app.rag.rerankers.base import BaseReranker

logger = logging.getLogger(__name__)

DASHSCOPE_RERANK_URL = (
    "https://dashscope.aliyuncs.com/api/v1/services/rerank/text-rerank/text-rerank"
)


class DashScopeReranker(BaseReranker):
    def __init__(
        self,
        api_key: str = "",
        model: str = "gte-rerank",
        top_n: int = 5,
        timeout: float = 10.0,
    ):
        self.api_key = api_key or settings.llm_api_key
        self.model = model
        self.top_n = top_n
        self.timeout = timeout

    async def rerank(
        self,
        query: str,
        documents: List[Document],
        top_n: int = 5,
    ) -> List[Document]:
        if not documents:
            return documents
        if top_n > len(documents):
            top_n = len(documents)

        # 提取文档文本作为候选列表
        doc_texts = [doc.page_content for doc in documents]

        payload = {
            "model": self.model,
            "input": {"query": query, "documents": doc_texts},
            "parameters": {"top_n": top_n, "return_documents": False},
        }

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(
                    DASHSCOPE_RERANK_URL, json=payload, headers=headers,
                )
                response.raise_for_status()
                result = response.json()
        except httpx.TimeoutException:
            logger.warning("DashScope Reranker timeout (%.1fs)", self.timeout)
            return documents[:top_n]  # 降级：返回粗排前N个
        except Exception as e:
            logger.error("DashScope Reranker API failed: %s", e)
            return documents[:top_n]  # 降级

        # 解析 API 返回的排序结果
        try:
            ranked_indices = []
            ranked_scores = []
            for item in result.get("output", {}).get("results", []):
                idx = item.get("index")
                score = item.get("relevance_score", 0.0)
                if idx is not None and 0 <= idx < len(documents):
                    ranked_indices.append(idx)
                    ranked_scores.append(score)

            reranked = []
            for idx, score in zip(ranked_indices, ranked_scores):
                doc = documents[idx]
                # 把精排分数写入 metadata，后续展示给用户
                doc.metadata["rerank_score"] = float(score)
                doc.metadata["retrieval_score"] = float(score)
                reranked.append(doc)

            logger.info(
                "Reranker: %d docs → %d docs (top_n=%d)",
                len(documents), len(reranked), top_n,
            )
            return reranked[:top_n]

        except Exception as e:
            logger.warning("Failed to parse Reranker response: %s", e)
            return documents[:top_n]  # 降级
