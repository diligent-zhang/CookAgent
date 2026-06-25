"""
认证业务逻辑层
"""

from datetime import datetime
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.security import create_access_token, hash_password, verify_password
from app.database.models import User


class AuthService:
    """认证服务"""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def register(
        self,
        username: str,
        password: str,
        nickname: str = "",
    ) -> User:
        """
        注册新用户。

        1. 检查用户名是否已存在
        2. 对密码做 bcrypt 哈希
        3. 创建 User 记录

        如果用户名已存在，抛出 ValueError。
        """
        # 检查用户名唯一性
        existing = await self.db.execute(
            select(User).where(User.username == username)
        )
        if existing.scalar_one_or_none() is not None:
            raise ValueError(f"用户名 '{username}' 已被注册")

        # 创建用户
        user = User(
            username=username,
            password_hash=hash_password(password),
            nickname=nickname or username,  # 没填昵称就用用户名
        )
        self.db.add(user)
        await self.db.commit()
        await self.db.refresh(user)
        return user

    async def login(self, username: str, password: str) -> Optional[tuple[str, User]]:
        """
        用户登录。

        1. 根据用户名查找用户
        2. 用 bcrypt 验证密码
        3. 更新 last_login_at
        4. 返回 (JWT 令牌, User)

        如果用户名不存在或密码错误，返回 None。
        """
        result = await self.db.execute(
            select(User).where(User.username == username)
        )
        user = result.scalar_one_or_none()

        if user is None:
            return None

        if not verify_password(password, user.password_hash):
            return None

        # 更新最后登录时间
        user.last_login_at = datetime.utcnow()
        await self.db.commit()

        return create_access_token(user.id), user

    async def get_user_by_id(self, user_id: str) -> Optional[User]:
        """根据 ID 查询用户。"""
        result = await self.db.execute(select(User).where(User.id == user_id))
        return result.scalar_one_or_none()


#   关键点解释：
#   - register 做唯一性检查后再插入——防止两个并发请求注册同一用户名
#     （生产环境最好加数据库 unique 约束，我们已经有了 unique=True）
#   - login 对"用户不存在"和"密码错误"返回相同的 None——防止用户名枚举攻击
#     （攻击者无法通过返回信息差异判断哪个用户名已注册）
#   - update_last_login_at 直接修改 ORM 对象属性 + commit，
#     SQLAlchemy 的 dirty-check 会自动生成 UPDATE 语句
