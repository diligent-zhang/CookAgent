"""
LLM 调用上下文管理

核心问题：在异步环境中，多个请求并发执行，如何让每个 LLM 调用
知道"自己属于哪个用户、哪个模块、哪个会话"？

解决方案：Python 标准库的 contextvars（上下文变量）
- 每个 asyncio.Task 有自己独立的 context 副本
- 不同 Task 之间互不干扰
- 不需要像 threading.local 那样手动绑定/解绑线程

使用方式（在 Agent 或 Conversation Service 中）：
    with llm_context("agent:default", user_id, session_id):
        response = await invoker.ainvoke(messages)
        # 在这个 with 块内的所有 LLM 调用都会自动带上这个上下文
"""
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class LLMCallContext:
    """
    单次 LLM 调用的上下文信息。
    由业务代码（Agent/Conversation Service）在调用 LLM 前设置，
    由回调处理器（callbacks.py）在 LLM 调用结束后读取并记录。
    """
    # 用 uuid4 生成唯一请求 ID，方便追踪一个请求内的多次 LLM 调用
    request_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    # 模块名，如 "agent:default"、"conversation"、"intent_detection"
    module_name: str = ""
    # 用户 ID，谁发起的请求
    user_id: str = ""
    # 会话 ID，属于哪条对话（Agent 和 Conversation 的会话 ID 不同）
    conversation_id: str = ""


# ===== 核心：ContextVar =====
# ContextVar 是 Python 3.7 引入的协程安全变量
# 每个 asyncio.Task 有自己独立的值，set 操作只影响当前 Task
# 类似于 threading.local，但适用于 asyncio 协程模型
_current_context: ContextVar[Optional[LLMCallContext]] = ContextVar(
    "llm_call_context",
    default=None,
)


def get_llm_context() -> Optional[LLMCallContext]:
    """获取当前协程的 LLM 调用上下文。如果没有设置，返回 None。"""
    return _current_context.get()


def set_llm_context(ctx: LLMCallContext) -> None:
    """设置当前协程的 LLM 调用上下文。"""
    _current_context.set(ctx)


@contextmanager
def llm_context(
    module_name: str,
    user_id: str = "",
    conversation_id: str = "",
):
    """
    上下文管理器：在 with 块内设置 LLM 调用上下文，退出时自动清除。

    用法：
        with llm_context("agent:default", user_id, session_id):
            response = await invoker.ainvoke(messages)

    原理：
        1. 进入 with 时：创建 LLMCallContext 并通过 ContextVar.set() 注入当前 Task
        2. LLM 调用中：LangChain 回调触发 callback.on_llm_end()
        3. callback.on_llm_end() 调用 get_llm_context() 读取上下文
        4. 退出 with 时：ContextVar.set(None) 清除，避免污染后续调用
    """
    ctx = LLMCallContext(
        module_name=module_name,
        user_id=user_id,
        conversation_id=conversation_id,
    )
    token = _current_context.set(ctx)
    try:
        yield ctx
    finally:
        # 恢复为进入 with 之前的值（通常是 None）
        _current_context.reset(token)
