"""
向量存储工厂

职责：创建和管理 Milvus 向量数据库的 Collection（集合/表）。

为什么选 Milvus 而不是 Chroma/FAISS/Pinecone？
- 原生支持 BM25 稀疏向量 → Dense + Sparse 双路混合检索
  不需要自己单独部署 Elasticsearch 做关键词匹配
- gRPC 协议，十亿级向量也能毫秒级返回
- 支持标量字段过滤（如按 user_id、category 过滤）
  我们用它存菜谱和用户文档两个 Collection，元数据过滤用标量字段实现

两个向量字段的作用：
  "dense"  → BGE 模型生成的 512 维稠密向量（语义匹配）
  "sparse" → BM25 算法生成的稀疏向量（关键词匹配）

  两者互补：
  - "番茄炒蛋怎么做" → dense 能匹配到"西红柿炒鸡蛋做法"（同义词）
  - "番茄炒蛋怎么做" → sparse 能精确匹配到标题含"番茄炒蛋"的菜谱（精确匹配）
"""
import logging
from typing import Any, Dict, List

from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings
from langchain_milvus import BM25BuiltInFunction, Milvus
from pymilvus import DataType, connections, utility

from app.config import settings

logger = logging.getLogger(__name__)

# ===== 标量字段定义 =====
# 定义文档元数据中哪些字段需要建索引，方便后续做过滤查询
# 例如：只查某个用户上传的文档、只查某个菜系的菜谱
METADATA_SCALAR_SCHEMA: Dict[str, Any] = {
    "category":      {"dtype": DataType.VARCHAR, "max_length": 128},
    "difficulty":    {"dtype": DataType.VARCHAR, "max_length": 64},
    "dish_name":     {"dtype": DataType.VARCHAR, "max_length": 256},
    "user_id":       {"dtype": DataType.VARCHAR, "max_length": 64},
    "parent_id":     {"dtype": DataType.VARCHAR, "max_length": 64},
    "source":        {"dtype": DataType.VARCHAR, "max_length": 256},
    "data_source":   {"dtype": DataType.VARCHAR, "max_length": 64},
    "is_dish_index": {"dtype": DataType.BOOL},
}


def get_vector_store(
    collection_name: str,
    embeddings: Embeddings,
    chunks: List[Document],
    milvus_config=None,
) -> Milvus:
    """
    获取或创建一个 Milvus Collection 实例。

    参数：
        collection_name: 集合名称（如 "cook_hero_recipes"）
        embeddings:      Embedding 模型实例（阶段4.1 创建的）
        chunks:          要索引的文档块列表
        milvus_config:   Milvus 连接配置，默认用 settings.milvus

    返回：
        一个已连接并可以执行相似度搜索的 Milvus 实例

    流程：
        1. 连接到 Milvus 服务器
        2. 检查 Collection 是否存在
        3. 如果不存在 → 创建 Collection，写入文档，生成 Dense + Sparse 向量
        4. 如果已存在 → 直接连接
    """
    if milvus_config is None:
        milvus_config = settings.milvus

    # ======== 组装连接参数 ========
    connection_args = {
        "host": milvus_config.host,
        "port": milvus_config.port,
    }
    if milvus_config.user:
        connection_args["user"] = milvus_config.user
    if milvus_config.password:
        connection_args["password"] = milvus_config.password

    alias = "default"

    # ===== 先检查 Collection 是否存在 =====
    # 连接后立即断开，只是做一次预检（避免保持不必要的长连接）
    logger.info(
        f"Checking Milvus at {connection_args['host']}:{connection_args['port']}"
    )

    try:
        connections.connect(alias=alias, **connection_args)
        collection_exists = utility.has_collection(collection_name, using=alias)
    finally:
        if connections.has_connection(alias):
            connections.disconnect(alias)

    # ===== 创建或连接 Collection =====
    if not collection_exists:
        logger.info(
            f"Collection '{collection_name}' not found, creating with "
            f"{len(chunks)} documents..."
        )
        # BM25BuiltInFunction 是 Milvus 2.4+ 内置的 BM25 稀疏向量生成器
        # 不需要自己实现 BM25 算法，Milvus 在写入文档时自动生成稀疏向量
        vector_store = Milvus.from_documents(
            documents=chunks,
            embedding=embeddings,
            collection_name=collection_name,
            connection_args=connection_args,
            text_field="text",
            vector_field=["dense", "sparse"],
            builtin_function=BM25BuiltInFunction(),
            metadata_schema=METADATA_SCALAR_SCHEMA,
        )
        logger.info(f"Created collection '{collection_name}'")
    else:
        logger.info(f"Connecting to existing collection '{collection_name}'")
        vector_store = Milvus(
            embedding_function=embeddings,
            collection_name=collection_name,
            connection_args=connection_args,
            text_field="text",
            vector_field=["dense", "sparse"],
            builtin_function=BM25BuiltInFunction(),
            metadata_schema=METADATA_SCALAR_SCHEMA,
        )
        logger.info(f"Connected to collection '{collection_name}'")
    return vector_store
