"""
GuardManager — 安全防线统一调度入口。

为什么需要一个 GuardManager 而不是在 main.py 里挨个调用？
  1. 统一入口：请求处理前一个方法搞定所有安全检查
  2. 审计集成：拦截后自动写入审计日志
  3. 可配置降级：安全组件出错时，是放行（fail-open）还是拒绝（fail-closed），
     由 GuardManager 统一控制，不需要在每个调用方判断
  4. 方便测试：Mock 一个 GuardManager 即可跳过所有安全检测
"""

import logging
from typing import Optional

from fastapi import Request, HTTPException

from app.security.middleware.rate_limiter import rate_limiter
from app.security.middleware.prompt_guard import prompt_guard

logger = logging.getLogger(__name__)


class GuardResult:
    """
    安全检测结果。

    为什么用数据类而不是直接抛异常？
      —— 把"检测"和"响应"分离。调用方可以根据 GuardResult
         决定是返回 429（速率限制）、400（内容检测）还是只记录日志。
    """

    def __init__(
        self,
        allowed: bool = True,
        block_reason: Optional[str] = None,
        status_code: int = 200,
    ):
        self.allowed = allowed
        self.block_reason = block_reason
        self.status_code = status_code


class GuardManager:
    """
    安全防线管理器。

    按顺序执行：RateLimiter → PromptGuard
    任何一个不通过就立即返回，不执行后续检查。
    这是经典的 Chain of Responsibility 模式。
    """

    async def check_request(
        self,
        request: Request,
        user_id: str,
        content: Optional[str] = None,
    ) -> GuardResult:
        """
        对请求执行完整安全检查。

        参数:
          request: FastAPI Request 对象
          user_id: 当前用户 ID
          content: 用户消息内容（GET 请求可能为 None，跳过内容检测）

        返回:
          GuardResult(allowed=True/False, reason=...)

        为什么按 RateLimiter → PromptGuard 这个顺序？
          —— 速率限制是最轻量的检测（Redis 一次 round trip），
             先拦截高频攻击，减少后续不必要的正则匹配。
          —— 如果反过来，攻击者可以用大量请求触发正则匹配，
             消耗 CPU 资源（ReDoS 风险）。
        """
        # 第一步：速率限制（最轻量）
        if rate_limiter:
            try:
                await rate_limiter.check(request, user_id)
            except HTTPException as e:
                return GuardResult(
                    allowed=False,
                    block_reason=f"rate_limited: {e.detail}",
                    status_code=e.status_code,
                )

        # 第二步：内容检测（仅对 POST/PUT 等有 body 的请求）
        if content and len(content) > 0:
            is_safe, reason = prompt_guard.check(content)
            if not is_safe:
                logger.warning(
                    f"[SECURITY] Prompt blocked: user={user_id}, "
                    f"reason={reason}, "
                    f"content_preview={content[:100]}"
                )
                return GuardResult(
                    allowed=False,
                    block_reason=reason,
                    status_code=400,
                )

        return GuardResult(allowed=True)


# ---- 单例 ----
# 无状态管理器，全局一个实例
guard_manager = GuardManager()
