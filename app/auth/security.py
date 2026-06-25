"""
安全工具：密码哈希与 JWT 令牌

密码哈希用 bcrypt — 自含盐值，不可逆，暴力破解慢
JWT 用 HS256 — 对称签名，服务端持有密钥即可验证
"""
from datetime import datetime, timedelta
from typing import Optional

from jose import JWTError, jwt
from passlib.context import CryptContext

from app.config import settings

# bcrypt 哈希上下文
# schemes=["bcrypt"] — 使用 bcrypt 算法
# deprecated="auto"  — 自动废弃过时的哈希方案
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# ---- 密码操作 ----

def hash_password(password: str) -> str:
    """对明文密码做 bcrypt 哈希，返回哈希字符串。"""
    return pwd_context.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """验证明文密码是否匹配已存储的哈希。"""
    return pwd_context.verify(plain_password, hashed_password)


# ---- JWT 操作 ----

def create_access_token(
    user_id: str,
    expires_delta: Optional[timedelta] = None,
) -> str:
    """
    创建 JWT 访问令牌。

    参数：
        user_id: 用户 ID（存入 token 的 sub 字段）
        expires_delta: 过期时间增量，默认 24 小时

    返回：
        编码后的 JWT 字符串
    """
    if expires_delta is None:
        expires_delta = timedelta(hours=24)

    now = datetime.utcnow()
    expire = now + expires_delta

    payload = {
        "sub": user_id,       # subject — 用户标识
        "iat": now,           # issued at — 签发时间
        "exp": expire,        # expiration — 过期时间
        "type": "access",     # token 类型（后续可加 refresh token）
    }
    return jwt.encode(payload, settings.JWT_SECRET_KEY, algorithm="HS256")


def decode_access_token(token: str) -> Optional[str]:
    """
    解析 JWT 令牌，返回其中的 user_id。
    如果令牌无效或过期，返回 None。
    """
    try:
        payload = jwt.decode(
            token,
            settings.JWT_SECRET_KEY,
            algorithms=["HS256"],
        )
        user_id: str = payload.get("sub")
        if user_id is None:
            return None
        return user_id
    except JWTError:
        return None


#   关键点解释：
#   - bcrypt 自带盐值（salt），同一个密码两次哈希结果不同，不需要额外存盐
#   - passlib.verify() 会自动从哈希串中提取盐值再比较，你不需要手动处理
#   - JWT 的 sub 字段按 RFC 7519 标准存放用户标识
#   - decode_access_token 返回 Optional[str]，上层调用方通过判空决定是否拒绝请求
