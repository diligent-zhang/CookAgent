"""
审计日志写入服务。

这段代码封装了审计日志的异步写入逻辑。
作用是将安全检测结果以非阻塞方式写入数据库，记录每一次请求的安全判定结果。

技术要点：
  - 使用独立的 async session：
    审计写入有自己的数据库会话，不跟业务请求共享事务。
    如果审计写入失败（数据库满了、连接超时），不会回滚业务数据。
  - fire-and-forget 模式：
    调用方只管 create_audit_log()，不等待写入结果。
    如果写入失败，logging.warning 记录，不抛异常到上层。
  - create_audit_log 是独立函数而非类方法：
    因为只需要一个数据库 session 参数，不需要状态。
    保持函数纯粹，方便单独测试。
"""

import logging
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.database.session import AsyncSessionLocal
from app.security.models import AuditLog

logger = logging.getLogger(__name__)


async def create_audit_log(
    user_id: Optional[str] = None,
    action: str = "",
    endpoint: str = "",
    status: str = "allowed",
    block_reason: Optional[str] = None,
    input_summary: Optional[str] = None,
    ip_address: Optional[str] = None,
    user_agent: Optional[str] = None,
    duration_ms: int = 0,
    token_count: int = 0,
):
    """
    写入一条审计日志到数据库。

    参数:
      status: "allowed" | "blocked" | "error"
        表示请求通过/被拦截/处理异常
      block_reason: 被拦截时说明具体原因，方便后续分析攻击模式
      input_summary: 用户输入的前 500 字符，不存完整内容是因为：
        - 节省存储空间（审计表可能非常大）
        - 完整内容已经在 messages 表中
        - 前 500 字符足够排查问题
    """
    safe_input = ""
    if input_summary:
        safe_input = input_summary[:500]

    try:
        async with AsyncSessionLocal() as session:
            log_entry = AuditLog(
                user_id=user_id,
                action=action,
                endpoint=endpoint,
                status=status,
                block_reason=block_reason,
                input_summary=safe_input,
                ip_address=ip_address,
                user_agent=user_agent[:500] if user_agent else None,
                duration_ms=duration_ms,
                token_count=token_count,
            )
            session.add(log_entry)
            await session.commit()
    except Exception as e:
        logger.warning(f"Failed to write audit log: {e}")


async def schedule_audit(
    user_id: Optional[str],
    action: str,
    endpoint: str,
    status: str = "allowed",
    block_reason: Optional[str] = None,
    input_content: Optional[str] = None,
    request=None,
    duration_ms: int = 0,
    token_count: int = 0,
):
    """
    调度审计日志写入（从 Request 提取元信息后调用 create_audit_log）。

    这段函数是审计写入的外部调用入口。
    它从 FastAPI Request 对象中提取 IP、User-Agent 等元信息，
    然后委托 create_audit_log 执行实际写入。

    为什么不在 create_audit_log 里直接取 IP 和 UA？
      —— create_audit_log 是纯数据写入函数，不依赖 Request 对象。
        把提取 IP/UA 的逻辑放在这里，保持 create_audit_log 简洁可测。
    """
    ip = None
    ua = None
    if request:
        ip = request.headers.get("X-Forwarded-For", "").split(",")[0].strip()
        if not ip:
            ip = request.client.host if request.client else "unknown"
        ua = request.headers.get("User-Agent", "")

    input_preview = ""
    if input_content:
        input_preview = input_content[:500]

    await create_audit_log(
        user_id=user_id,
        action=action,
        endpoint=endpoint,
        status=status,
        block_reason=block_reason,
        input_summary=input_preview,
        ip_address=ip,
        user_agent=ua,
        duration_ms=duration_ms,
        token_count=token_count,
    )
