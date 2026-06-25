"""
FastAPI 认证依赖

用法：
    @router.get("/protected")
    async def protected_route(current_user = Depends(get_current_user)):
        ...
"""

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.security import decode_access_token
from app.database.models import User
from app.database.session import AsyncSessionLocal

# HTTPBearer 自动从请求头 Authorization: Bearer <token> 中提取令牌
security_scheme = HTTPBearer()


async def get_db() -> AsyncSession:
    """获取数据库会话（每个请求一个 session）。"""
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security_scheme),
    db: AsyncSession = Depends(get_db),
) -> User:
    """
    从 JWT 令牌中解析 user_id，查询数据库返回 User 对象。

    如果令牌无效或用户不存在，抛出 401 Unauthorized。
    """
    token = credentials.credentials
    user_id = decode_access_token(token)

    if user_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="无效或过期的认证令牌",
        )

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()

    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="用户不存在",
        )

    return user


#   关键点解释：
#   - HTTPBearer 是 FastAPI 内置的安全方案，自动解析 Authorization: Bearer xxx 头
#   - get_db 用生成器模式创建/销毁 session，保证每个请求的事务隔离
#   - get_current_user 做两步校验：先验证 JWT 有效性，再查数据库确认用户存在
#     ——防止已删除用户拿着旧令牌访问
