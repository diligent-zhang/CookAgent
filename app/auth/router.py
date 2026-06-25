"""
认证相关 API 路由
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user, get_db
from app.auth.schemas import (
    AuthResponse,
    UserLoginRequest,
    UserRegisterRequest,
    UserResponse,
)
from app.auth.security import create_access_token
from app.auth.service import AuthService
from app.database.models import User

router = APIRouter(prefix="/auth", tags=["认证"])


@router.post("/register", response_model=AuthResponse, status_code=status.HTTP_201_CREATED)
async def register(
    body: UserRegisterRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    用户注册。
    创建新用户账号，自动登录并返回 JWT 令牌。
    """
    service = AuthService(db)
    try:
        user = await service.register(
            username=body.username,
            password=body.password,
            nickname=body.nickname,
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(e),
        )
    token = create_access_token(user.id)
    return AuthResponse(access_token=token, user=user)


@router.post("/login", response_model=AuthResponse)
async def login(
    body: UserLoginRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    用户登录。

    使用用户名和密码换取 JWT 访问令牌。
    后续请求在 Authorization 头中携带此令牌：
        Authorization: Bearer <access_token>
    """
    service = AuthService(db)
    result = await service.login(body.username, body.password)

    if result is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="用户名或密码错误",
        )
    return AuthResponse(access_token=result[0], user=result[1])


@router.get("/me", response_model=UserResponse)
async def get_me(current_user: User = Depends(get_current_user)):
    """
    获取当前登录用户的信息。

    需要在请求头中携带有效的 JWT 令牌。
    用于前端判断登录状态、展示用户昵称。
    """
    return current_user


#   关键点解释：
#   - /register 返回 201 Created（资源创建成功），不是 200
#   - /login 失败统一返回 401 + "用户名或密码错误"，不区分是哪个错
#   - /me 依赖 get_current_user，令牌无效时自动返回 401，路由函数不需要再处理
