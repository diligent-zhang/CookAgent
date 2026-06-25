"""
Embedding 模型工厂

职责：加载和管理文本向量化模型。

切换为阿里云 DashScope Embedding API（替代被墙的 HuggingFace 本地模型）：
  - 模型：text-embedding-v3（1024维）
  - API 地址：https://dashscope.aliyuncs.com/
  - 通过 LLM_API_KEY 认证（与 qwen-plus/qwen-max 共用同一 API Key）
"""
import logging
import os

from langchain_core.embeddings import Embeddings

logger = logging.getLogger(__name__)


def get_embedding_model(model_name: str = "text-embedding-v3") -> Embeddings:
    """
    创建 DashScope Embedding 实例。

    参数：
        model_name: DashScope embedding 模型名，默认 text-embedding-v3

    返回：
        实现了 LangChain Embeddings 接口的 DashScopeEmbeddings 实例
    """
    from langchain_community.embeddings import DashScopeEmbeddings

    api_key = os.environ.get("LLM_API_KEY", "")
    if not api_key:
        logger.warning("LLM_API_KEY not set in environment, embedding may fail")

    logger.info(f"Using DashScope embedding model: {model_name}")

    return DashScopeEmbeddings(
        model=model_name,
        dashscope_api_key=api_key,
    )
