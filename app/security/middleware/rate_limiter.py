"""
速率限制中间件 — Redis 滑动窗口算法

为什么选 Sliding Window 而不是 Fixed Window？
   问题场景：Fixed Window（每分钟 60 次）
     - 用户在 12:00:59 发了 60 次请求
     - 在 12:01:00 又发了 60 次请求
     - 实际 2 秒内发出了 120 次请求，绕过了限制

   Sliding Window 用 ZSET 存储每个请求的时间戳，每次检查时
   只统计 [now - window_seconds, now] 范围内的请求。
   不会出现窗口边界突发问题，限流曲线更平滑。

为什么用 Redis 而不是内存字典？
   —— 内存字典在多进程（uvicorn workers）间不共享
   —— 内存字典进程重启后丢失
   —— Redis 原子操作天然线程/进程安全
   —— 我们已经有了 Redis 依赖（RAG 缓存），不需要新基础设施
"""

import time
import uuid
from typing import Optional

from fastapi import Request, HTTPException
from redis.asyncio import Redis

from app.config import settings


class RateLimiter:
    """
    滑动窗口速率限制器。

    Redis 数据结构设计：
      Key:    rate_limit:{user_id}:{endpoint_path}
      Value:  ZSET
        member: 唯一请求 ID（timestamp + random suffix 防止同毫秒冲突）
        score:  请求发生的 Unix 时间戳（秒）

    为什么 member 用 timestamp + random 而不是只有 timestamp？
      —— ZSET 不允许重复 member。如果两个请求在同一毫秒到达，
         只用 timestamp 会导致后者覆盖前者（member 相同），计数少 1。
         加上 random suffix 保证每个 member 唯一。
    """

    # Redis key 前缀，方便批量管理（如 `KEYS rate_limit:*` 查看所有限流 key）
    KEY_PREFIX = "rate_limit"

    def __init__(self, redis_client: Redis):
        self.redis = redis_client

    def _build_key(self, user_id: str, path: str) -> str:
        """构建 Redis key。按 user + endpoint 独立计数。"""
        return f"{self.KEY_PREFIX}:{user_id}:{path}"

    async def check(self, request: Request, user_id: str) -> None:
        """
        检查当前请求是否超出速率限制。
        如果超出，抛出 HTTPException 429 让 FastAPI 处理。
        如果未超出，在 Redis 中记录本次请求。

        为什么方法不叫 is_allowed 而是叫 check 并直接抛异常？
          —— 遵循 FastAPI 惯例，中间件的检查方法是"不通过就抛异常"，
             调用方不需要处理返回值，代码更简洁。
             如果你想只检查不抛异常（比如做预警），可以再加一个 try_check 方法。
        """
        if not settings.rate_limit.enabled:
            return  # 配置关闭时不检查，方便开发环境

        if not self.redis:
            return  # Redis 不可用时降级放行，不应该因为监控组件故障而阻断业务

        # 1. 确定该路径的限流配置
        limit_config = settings.rate_limit.get_limit(request.url.path)
        key = self._build_key(user_id, request.url.path)
        now = time.time()
        window_start = now - limit_config.window_seconds

        # 2. 清理窗口外的过期记录 + 统计当前窗口内的请求数
        # 为什么清理和统计放在一个 pipeline 里？
        #   —— Redis pipeline 保证原子性，不会出现"清理了但计数时
        #      又有新请求插入"的竞争状态
        # ZREMRANGEBYSCORE: O(log(N) + M)，N=ZSET 大小，M=被删除元素数
        # ZCARD: O(1)
        try:
            async with self.redis.pipeline(transaction=True) as pipe:
                pipe.zremrangebyscore(key, 0, window_start)
                pipe.zcard(key)
                _, current_count = await pipe.execute()

            # 3. 判断是否超出限制
            if current_count >= limit_config.max_requests:
                # 重试时间 = 窗口结束时间 - 当前时间（向上取整）
                retry_after = int(window_start + limit_config.window_seconds - now) + 1
                raise HTTPException(
                    status_code=429,
                    detail="Too many requests. Please slow down.",
                    headers={"Retry-After": str(retry_after)},
                )

            # 4. 记录本次请求（ZADD, score 为当前时间戳）
            # member = f"{now}:{random_part}" 保证唯一
            member = f"{now}:{uuid.uuid4().hex[:8]}"
            await self.redis.zadd(key, {member: now})

            # 5. 设置 key 过期时间 = 窗口的 2 倍
            # 为什么设 2 倍而不是等于窗口？
            #   —— 防止时钟抖动导致 key 提前过期
            #   —— 如果设等于窗口，窗口刚好结束瞬间 key 被删，
            #      新的请求会从 0 开始计数，可能出现短暂超发
            await self.redis.expire(key, limit_config.window_seconds * 2)

        except HTTPException:
            raise  # 重新抛出自定义异常
        except Exception:
            # Redis 挂了怎么办？
            # 降级策略：放行请求。理由是：速率限制是防护措施，不是核心功能。
            # 不能因为防护组件故障而让整个服务不可用（这不符合"安全组件本身
            # 应该是高可用的"原则）。
            # 如果你需要更严格的 fail-closed 策略（Redis 挂=拒绝所有），
            # 把这里的 pass 改成 raise HTTPException(503)。
            pass


# ---- 单例 ----
# 为什么是模块级单例而不是类上的 static？
# —— 模块级是 Python 最自然的单例：导入后就一个实例。
#    不需要 Singleton 模式的各种 trick。
# 为什么在 main.py 的 lifespan 中初始化而不是在这里？
# —— redis_client 在 main.py 创建，这里需要一个地方接收注入。
#    用全局变量 + init 函数实现简单的依赖注入。
rate_limiter: Optional[RateLimiter] = None


def init_rate_limiter(redis_client) -> Optional[RateLimiter]:
    """由 main.py 在启动时调用，传入全局 Redis 客户端（可能为 None）。"""
    global rate_limiter
    if redis_client is not None:
        rate_limiter = RateLimiter(redis_client)
    return rate_limiter
