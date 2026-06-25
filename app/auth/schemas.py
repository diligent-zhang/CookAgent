"""
认证相关的 Pydantic 模型（请求/响应 schema）
"""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


# ---- 请求体 ----

class UserRegisterRequest(BaseModel):
    """注册请求"""
    username: str = Field(
        ...,
        min_length=3,
        max_length=64,
        pattern=r"^[a-zA-Z0-9_]+$",
        description="用户名，只允许英文、数字、下划线",
        examples=["zhangsan"],
    )

    password: str = Field(
        ...,
        min_length=6,
        max_length=128,
        description="密码，最少 6 位",
    )
    nickname: Optional[str] = Field(
        default="",
        max_length=128,
        description="昵称（可选）",
    )


class UserLoginRequest(BaseModel):
    """登录请求。"""
    username: str = Field(..., description="用户名")
    password: str = Field(..., description="密码")


# ---- 响应体 ----

class UserResponse(BaseModel):
    """公开的用户信息（脱敏，不返回密码哈希）。"""
    id: str
    username: str
    nickname: str
    created_at: datetime
    last_login_at: Optional[datetime] = None

    class Config:
        from_attributes = True  # 允许从 ORM 对象直接构造


class TokenResponse(BaseModel):
    """登录成功后返回的令牌。"""
    access_token: str = Field(..., description="JWT 访问令牌")
    token_type: str = Field(default="bearer", description="令牌类型")


class AuthResponse(BaseModel):
    """认证成功返回的完整响应（令牌 + 用户信息）。"""
    access_token: str = Field(..., description="JWT 访问令牌")
    token_type: str = Field(default="bearer", description="令牌类型")
    user: UserResponse


#   关键点解释：
#   - 请求体做严格校验：用户名只允许 [a-zA-Z0-9_]，密码最少 6 位
#   - 响应体 UserResponse 不包含 password_hash——永远不要通过 API 返回密码哈希
#   - from_attributes = True（Pydantic v2）替代了 v1 的 orm_mode = True
