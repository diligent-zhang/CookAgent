"""
CookHero 应用入口
"""
import asyncio
import sys

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.llm.provider import LLMProvider
from app.rag.service import RAGService

# ---- 全局服务实例 ----
llm_provider = LLMProvider()

# Redis 客户端（L1 精确匹配缓存）
redis_client = None
if settings.redis.host:
    try:
        import redis.asyncio as redis
        redis_client = redis.Redis(
            host=settings.redis.host,
            port=settings.redis.port,
            db=settings.redis.db,
            password=settings.redis.password or None,
            decode_responses=True,
        )
    except ImportError:
        pass  # redis 未安装时跳过

rag_service = RAGService(redis_client)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # ===== 启动阶段 =====
    print(f"[STARTUP] {settings.PROJECT_NAME} is starting...")

    # 初始化数据库
    from app.database.session import init_db
    await init_db()
    print("[STARTUP] Database initialized.")

    # 初始化安全模块（依赖 Redis）
    from app.security.middleware.rate_limiter import init_rate_limiter
    init_rate_limiter(redis_client)
    print("[STARTUP] Security module initialized.")

    # 初始化 RAG 服务（加载 Embedding 模型、连接 Milvus）
    await rag_service.initialize()
    print("[STARTUP] RAG service initialized.")

    # 将全局服务注入到 Conversation 模块
    from app.conversation.router import init_conversation_module
    init_conversation_module(llm_provider, rag_service)
    print("[STARTUP] Conversation module initialized.")

    # 将全局服务注入到 Agent 模块
    from app.agent import init_agent_module
    init_agent_module(llm_provider, rag_service)
    print("[STARTUP] Agent module initialized.")

    yield

    # ===== 关闭阶段 =====
    from app.database.session import close_db
    await close_db()
    print(f"[SHUTDOWN] {settings.PROJECT_NAME} is shutting down...")


app = FastAPI(
    title=settings.PROJECT_NAME,
    description="智能饮食助手后端 API",
    version="0.2.0",
    lifespan=lifespan,
)

# CORS 中间件
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://localhost:3000",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---- 注册路由 ----
from app.auth import auth_router
app.include_router(auth_router, prefix=settings.API_V1_STR)

from app.conversation.router import router as conversation_router
app.include_router(conversation_router, prefix=settings.API_V1_STR)

from app.agent import agent_router
app.include_router(agent_router, prefix=settings.API_V1_STR)


@app.get("/")
async def root():
    return {"message": f"Welcome to {settings.PROJECT_NAME} API!"}
