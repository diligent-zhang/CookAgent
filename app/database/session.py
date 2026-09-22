"""
数据库会话管理
负责创建异步数据库引擎、会话工厂，以及启动/关闭时的初始化。
"""

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.config import settings

# ===== 1. 异步数据库引擎 =====
engine = create_async_engine(
    f"postgresql+psycopg://{settings.postgres.user}:{settings.postgres.password}"
    f"@{settings.postgres.host}:{settings.postgres.port}/{settings.postgres.database}",
    pool_size=settings.postgres.pool_size,
    max_overflow=settings.postgres.max_overflow,
    pool_pre_ping=True,
    echo=settings.postgres.echo,  # True 时打印 SQL（调试用）
)

# ===== 2. 异步会话工厂 =====
AsyncSessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,  # 提交后不使对象过期，方便在 commit 后继续访问属性
)


# ===== 3. ORM 基类 =====
class Base(DeclarativeBase):
    pass


# ===== 4. 数据库生命周期管理 =====

async def init_db():
    """
    应用启动时调用：根据 ORM 模型自动创建表。
    注意：生产环境应该用 Alembic 做 migration，这里用 create_all 简化开发。
    """
    import app.agent.models  # noqa: F401 确保 Agent 表模型已注册到 Base
    import app.diet.models  # noqa: F401 确保饮食模块表模型已注册到 Base
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def close_db():
    """应用关闭时调用：释放连接池资源。"""
    await engine.dispose()
