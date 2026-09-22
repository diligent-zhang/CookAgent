"""
全局 Settings 单例
负责把 load_config() 返回的字典转换为结构化属性访问。
阶段1只定义 LLM 和 DB 相关的最简配置类。
"""

import os

from app.config.config_loader import load_config


class LLMProfileConfig:
    """单个 LLM 层的配置（fast 或 normal）。"""

    def __init__(self, data: dict):
        self.model_names: list = data.get("model_names", [])
        self.base_url: str = data.get("base_url", "")
        self.temperature: float = data.get("temperature", 1.0)
        self.max_tokens: int = data.get("max_tokens", 8192)

    def pick_default_model(self) -> str:
        """选一个默认模型名（后续 Provider 会随机选做负载均衡）。"""
        return self.model_names[0] if self.model_names else ""


class LLMConfig:
    """LLM 总配置，包含 fast 和 normal 两层。"""

    def __init__(self, data: dict):
        self.fast = LLMProfileConfig(data.get("fast", {}))
        self.normal = LLMProfileConfig(data.get("normal", {}))

    def get_profile(self, llm_type: str | None = None) -> LLMProfileConfig:
        """根据类型名获取对应的 profile。"""
        if llm_type == "fast":
            return self.fast
        return self.normal  # 默认使用 normal


class PostgresConfig:
    """PostgreSQL 连接配置。"""

    def __init__(self, data: dict):
        self.host: str = data.get("host", "localhost")
        self.port: int = data.get("port", 5432)
        self.database: str = data.get("database", "")
        self.user: str = data.get("user", "")
        self.password: str = data.get("password", "")
        self.pool_size: int = data.get("pool_size", 20)
        self.max_overflow: int = data.get("max_overflow", 30)
        self.pool_timeout: int = data.get("pool_timeout", 30)
        self.pool_recycle: int = data.get("pool_recycle", 1800)
        self.echo: bool = data.get("echo", False)


class RedisConfig:
    """Redis 连接配置。"""

    def __init__(self, data: dict):
        self.host: str = data.get("host", "localhost")
        self.port: int = data.get("port", 6379)
        self.db: int = data.get("db", 0)
        self.password: str = data.get("password", "")


class MilvusConfig:
    """Milvus 向量数据库连接配置。"""

    def __init__(self, data: dict):
        self.host: str = data.get("host", "localhost")
        self.port: int = data.get("port", 19530)
        self.user: str = data.get("user", "")
        self.password: str = data.get("password", "")
        self.secure: bool = data.get("secure", False)


class EmbeddingConfig:
    """Embedding 模型配置。"""

    def __init__(self, data: dict):
        self.model_name: str = data.get("model_name", "text-embedding-v3")


class VectorStoreConfig:
    """向量存储配置。"""

    def __init__(self, data: dict):
        self.type: str = data.get("type", "milvus")
        self.collection_names: dict = data.get("collection_names", {
            "recipes": "cook_hero_recipes",
            "personal": "cook_hero_personal_docs",
        })


class RetrievalConfig:
    """RAG 检索参数配置。"""

    def __init__(self, data: dict):
        self.top_k: int = data.get("top_k", 9)
        self.score_threshold: float = data.get("score_threshold", 0.2)
        self.ranker_type: str = data.get("ranker_type", "weighted")
        self.ranker_weights: list = data.get("ranker_weights", [0.8, 0.2])


class CacheConfig:
    """缓存配置。"""

    def __init__(self, data: dict):
        self.enabled: bool = data.get("enabled", True)
        self.ttl: int = data.get("ttl", 3600)
        self.l2_enabled: bool = data.get("l2_enabled", True)
        self.similarity_threshold: float = data.get("similarity_threshold", 0.92)
        self.vector_collection: str = data.get("vector_collection", "cookhero_retrieval_cache")


class RateLimitEndpointConfig:
    """
    单个 endpoint 的速率限制配置。

    为什么不用嵌套字典而用类？
    —— 字典访问容易拼错 key，类属性有 IDE 自动补全和类型提示，
       写错时 Python 直接报 AttributeError，而不是静默返回 None。
    """

    def __init__(self, data: dict):
        self.max_requests: int = data.get("max_requests", 60)
        self.window_seconds: int = data.get("window_seconds", 60)


class RateLimitConfig:
    """
    速率限制总配置。

    支持两层控制：
      1. default — 所有 endpoint 的默认限制
      2. endpoints — 按路由路径覆盖限制

    为什么 Agent chat 比普通对话限制更严？因为 Agent 每次请求可能触发多次 LLM
    调用（思考 + 工具调用 + 最终回答），成本是普通对话的 3-5 倍。
    """

    def __init__(self, data: dict):
        self.enabled: bool = data.get("enabled", True)
        # 默认限制：每分钟 60 次
        self.default = RateLimitEndpointConfig(data.get("default", {}))
        # 按 endpoint 路径的覆盖配置
        self.endpoints: dict[str, RateLimitEndpointConfig] = {}
        for path, ep_data in data.get("endpoints", {}).items():
            self.endpoints[path] = RateLimitEndpointConfig(ep_data)

    def get_limit(self, path: str) -> RateLimitEndpointConfig:
        """
        根据请求路径返回对应的限制配置。

        为什么用最长前缀匹配而不是精确匹配？
        —— 像 /api/v1/conversations/{id}/chat 这种动态路径，
           配置时只需要写 /api/v1/conversations/ 前缀即可覆盖所有对话路由。
        """
        best_match = self.default
        best_len = 0
        for pattern, config in self.endpoints.items():
            if path.startswith(pattern) and len(pattern) > best_len:
                best_match = config
                best_len = len(pattern)
        return best_match


class PromptGuardConfig:
    """
    Prompt 输入检测配置。

    为什么 LLM 检测默认关闭？
    —— 每次 LLM 判断有 200ms 延迟和 API 费用，而正则规则引擎能拦截 90% 的常见攻击。
       LLM 检测留给高安全要求的场景按需开启。

    max_message_length 设为 4000：正常菜谱查询不会超过这个长度，
    超过的基本是注入攻击尝试或恶意灌入大量文本。
    """

    def __init__(self, data: dict):
        self.enabled: bool = data.get("enabled", True)
        self.max_message_length: int = data.get("max_message_length", 4000)
        # 内置的拒绝模式类型，这些对应的正则规则在 rules.py 中定义
        self.deny_patterns: list = data.get("deny_patterns", [
            "sql_injection",
            "command_injection",
            "prompt_leak",
            "jailbreak",
        ])
        # LLM 二次检测（默认关闭，按需开启）
        self.llm_check_enabled: bool = data.get("llm_check", {}).get("enabled", False)
        self.llm_check_threshold: float = data.get("llm_check", {}).get("threshold", 0.7)


class Settings:
    """
    全局设置单例。

    用法：
        from app.config import settings
        print(settings.llm.fast.model_names)  # → ['qwen-plus']
    """

    def __init__(self):
        raw = load_config()

        self.PROJECT_NAME: str = "CookHero"
        self.API_V1_STR: str = "/api/v1"

        # LLM 配置
        self.llm = LLMConfig(raw.get("llm", {}))

        # Embedding 配置
        self.embedding = EmbeddingConfig(raw.get("embedding", {}))

        # 向量存储配置
        self.vector_store = VectorStoreConfig(raw.get("vector_store", {}))

        # RAG 检索参数
        self.retrieval = RetrievalConfig(raw.get("retrieval", {}))

        # 缓存配置
        self.cache = CacheConfig(raw.get("cache", {}))

        # 数据库配置
        db_raw = raw.get("database", {})
        self.postgres = PostgresConfig(db_raw.get("postgres", {}))
        self.redis = RedisConfig(db_raw.get("redis", {}))
        self.milvus = MilvusConfig(db_raw.get("milvus", {}))

        # 安全配置
        security_raw = raw.get("security", {})
        self.rate_limit = RateLimitConfig(security_raw.get("rate_limit", {}))
        self.prompt_guard = PromptGuardConfig(security_raw.get("prompt_guard", {}))

        # ===== P1 新增：意图识别与查询改写配置 =====
        conv_raw = raw.get("conversation", {})
        intent_raw = conv_raw.get("intent_detection", {})
        self.intent_detection_enabled: bool = intent_raw.get("enabled", True)
        self.intent_cache_ttl: int = intent_raw.get("cache_ttl", 3600)
        self.intent_fallback: str = intent_raw.get("fallback_intent", "recipe_search")
        self.intent_timeout: int = intent_raw.get("timeout", 2)

        rewrite_raw = conv_raw.get("query_rewriting", {})
        self.query_rewriting_enabled: bool = rewrite_raw.get("enabled", True)
        self.query_rewrite_max_length: int = rewrite_raw.get("max_rewrite_length", 200)

        # ===== P1 新增：Agent 配置 =====
        agent_raw = raw.get("agent", {})
        self.enabled_agents: list = agent_raw.get(
            "enabled_agents", ["recipe_master", "general"]
        )

        # ===== P1 新增：Reranker 配置 =====
        rag_raw = raw.get("rag", {})
        reranker_raw = rag_raw.get("reranker", {})
        self.reranker_enabled: bool = reranker_raw.get("enabled", True)
        self.reranker_model: str = reranker_raw.get("model", "gte-rerank")
        self.reranker_top_n: int = reranker_raw.get("top_n", 5)

        metadata_filter_raw = rag_raw.get("metadata_filter", {})
        self.metadata_filter_enabled: bool = metadata_filter_raw.get("enabled", True)

        # ===== P2 新增：饮食管理配置 =====
        diet_raw = raw.get("diet", {})
        diet_ai_raw = diet_raw.get("ai_parsing", {})
        self.diet_enabled: bool = diet_raw.get("enabled", True)
        self.diet_ai_parsing_enabled: bool = diet_ai_raw.get("enabled", True)

        # JWT 密钥（从 .env 加载，已在 load_config 中注入）
        self.JWT_SECRET_KEY: str = (
            raw.get("JWT_SECRET_KEY", "")
            or os.environ.get("JWT_SECRET_KEY", "")
        )

        # LLM API Key：优先用 FAST_LLM_API_KEY，回退到 LLM_API_KEY
        self.llm_api_key: str = (
            raw.get("FAST_LLM_API_KEY", "")
            or raw.get("LLM_API_KEY", "")
            or os.environ.get("LLM_API_KEY", "")
        )


# 全局单例
settings = Settings()
