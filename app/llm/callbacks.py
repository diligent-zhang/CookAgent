"""
LLM Usage 追踪回调处理器

核心机制：LangChain 的 Callback 系统
- ChatOpenAI 在调用 LLM 的前后会触发一系列回调事件
- 我们通过继承 BaseCallbackHandler 来"监听"这些事件
- 在 on_llm_end 中捕获 token 使用量并异步写入数据库

回调事件生命周期（一次 LLM 调用）：
    on_llm_start  →  LLM 推理  →  on_llm_end
         ↑                          ↑
    记录开始时间              计算耗时、提取 token 用量、写入 DB

为什么用后台线程 + 独立事件循环写数据库？
- 如果直接在回调中 await，会阻塞 LLM 调用返回
- 回调本身是同步的（LangChain 设计），但数据库操作是异步的
- 解决方案：run_coroutine_threadsafe 把异步写 DB 任务提交到后台事件循环
"""
import asyncio
import logging
import threading
import time
from typing import Any, Dict, List, Optional
from uuid import UUID

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.outputs import LLMResult

from app.llm.context import get_llm_context

logger = logging.getLogger(__name__)

# ===== 后台事件循环（用于异步写入数据库） =====

_background_loop: Optional[asyncio.AbstractEventLoop] = None
_background_thread: Optional[threading.Thread] = None


def _get_background_loop() -> asyncio.AbstractEventLoop:
    """
    获取或创建后台事件循环。
    这是一个在独立线程中运行的 asyncio 事件循环。
    主线程的同步回调通过 run_coroutine_threadsafe 把写 DB 任务提交到这里。
    """
    global _background_loop, _background_thread

    if _background_loop is None or not _background_loop.is_running():
        # 在独立线程中启动一个新的事件循环
        _background_loop = asyncio.new_event_loop()
        _background_thread = threading.Thread(
            target=_background_loop.run_forever,  # 让事件循环一直运行
            daemon=True,                          # 守护线程：主进程退出时自动结束
            name="llm-usage-logger",
        )
        _background_thread.start()

    return _background_loop


# ===== LangChain 回调处理器 =====

class LLMUsageCallbackHandler(BaseCallbackHandler):
    """
    在每次 LLM 调用完成时自动捕获 token 使用信息并写入数据库。

    LangChain 调用链中的角色：
        ChatOpenAI.ainvoke(messages)
          → 触发 on_llm_start()     ← 我们记录 start_time
          → 调用 DashScope API
          → 得到 LLMResult
          → 触发 on_llm_end()       ← 我们提取 usage，异步写 DB
    """

    def __init__(self):
        super().__init__()
        # 字典：run_id(str) → 开始时间戳(float)
        # 因为一次请求可能触发多次 LLM 调用（如 Agent ReAct 循环），
        # 所以用 run_id 来区分每一次独立的 LLM 调用
        self._start_time: Dict[str, float] = {}

    # ---- LangChain 回调钩子 ----

    def on_llm_start(
        self,
        serialized: Dict[str, Any],
        prompts: List[str],
        *,
        run_id: UUID,
        **kwargs: Any,
    ) -> None:
        """
        LLM 调用开始时触发。
        记录开始时间，用于后续计算 LLM 推理耗时。
        """
        self._start_time[str(run_id)] = time.time()

    def on_llm_end(
        self,
        response: LLMResult,
        *,
        run_id: UUID,
        **kwargs: Any,
    ) -> None:
        """
        LLM 调用完成时触发。
        这是核心逻辑：
        1. 计算本次调用耗时
        2. 通过 get_llm_context() 获取上下文（谁调用的？属于哪个会话？）
        3. 从 LLMResult 中提取 token 用量和模型名
        4. 提交到后台线程异步写入数据库
        """
        # 计算耗时（毫秒）
        start = self._start_time.pop(str(run_id), time.time())
        duration_ms = int((time.time() - start) * 1000)

        # 获取调用上下文（由 context.py 的 llm_context() 设置）
        ctx = get_llm_context()
        if not ctx:
            # 没有上下文意味着业务代码忘记设置 llm_context()，
            # 这是开发期间的配置问题，跳过记录
            logger.debug("No LLM context set, skipping usage logging")
            return

        # 提取信息并写入
        usage_data = self._extract_usage(response)
        model_name = self._extract_model_name(response)
        tool_name = self._extract_tool_name(response)

        log_data = {
            "request_id": ctx.request_id,
            "module_name": ctx.module_name,
            "user_id": ctx.user_id,
            "conversation_id": ctx.conversation_id,
            "model_name": model_name,
            "tool_name": tool_name,
            "input_tokens": (
                usage_data.get("input_tokens")
                or usage_data.get("prompt_tokens")
                if usage_data
                else None
            ),
            "output_tokens": (
                usage_data.get("output_tokens")
                or usage_data.get("completion_tokens")
                if usage_data
                else None
            ),
            "total_tokens": usage_data.get("total_tokens") if usage_data else None,
            "duration_ms": duration_ms,
        }

        # 提交到后台事件循环异步写入数据库
        self._schedule_write(log_data)

    # ---- Token 用量提取 ----

    def _get_first_generation(self, response: LLMResult):
        """
        从 LLMResult 中取第一个 generation。
        LLMResult 结构：generations[0][0] → 第一个候选回答的第一个消息
        """
        if response.generations and response.generations[0]:
            return response.generations[0][0]
        return None

    def _extract_usage(self, response: LLMResult) -> Optional[Dict[str, Any]]:
        """
        从 LLMResult 中提取 token 使用信息。

        兼容两种格式：
        1. 非流式：response.llm_output["token_usage"] = {input_tokens, output_tokens, total_tokens}
        2. 流式：response.generations[0][0].message.usage_metadata（LangChain 自动聚合）
        """
        # 方式 1：标准 llm_output（非流式调用）
        if response.llm_output:
            if token_usage := response.llm_output.get("token_usage"):
                return token_usage
            # 有些模型直接把 total_tokens 放在 llm_output 里
            if "total_tokens" in response.llm_output:
                return response.llm_output

        # 方式 2：usage_metadata（流式调用，LangChain 1.x 自动聚合）
        gen = self._get_first_generation(response)
        if gen:
            message = getattr(gen, "message", None)
            if message and hasattr(message, "usage_metadata"):
                metadata = getattr(message, "usage_metadata", None)
                if metadata:
                    if isinstance(metadata, dict):
                        return metadata
                    # UsageMetadata 对象（LangChain 1.x 新格式）
                    return {
                        "input_tokens": getattr(metadata, "input_tokens", None),
                        "output_tokens": getattr(metadata, "output_tokens", None),
                        "total_tokens": getattr(metadata, "total_tokens", None),
                    }

        return None

    def _extract_model_name(self, response: LLMResult) -> Optional[str]:
        """从 LLMResult 中提取实际使用的模型名称（如 qwen-max）。"""
        # 标准格式
        if response.llm_output:
            if model := (
                response.llm_output.get("model_name")
                or response.llm_output.get("model")
            ):
                return model

        # 从 response_metadata 中提取（某些模型返回在这里）
        gen = self._get_first_generation(response)
        if gen:
            message = getattr(gen, "message", None)
            if message and hasattr(message, "response_metadata"):
                metadata = getattr(message, "response_metadata", None)
                if metadata:
                    if model := metadata.get("model_name") or metadata.get("model"):
                        return model

        return None

    def _extract_tool_name(self, response: LLMResult) -> Optional[str]:
        """
        从 LLMResult 中提取工具调用名称。
        只取第一个 tool call 的名称，用于统计"哪个工具被 LLM 调用最多"。
        """
        gen = self._get_first_generation(response)
        if not gen:
            return None

        message = getattr(gen, "message", None)
        if message and hasattr(message, "tool_calls"):
            tool_calls = getattr(message, "tool_calls", None)
            if tool_calls and isinstance(tool_calls, list) and tool_calls:
                tool_call = tool_calls[0]
                if isinstance(tool_call, dict):
                    return tool_call.get("name") or tool_call.get("function", {}).get("name")
                elif hasattr(tool_call, "name"):
                    return tool_call.name

        return None

    # ---- 异步写数据库 ----

    def _schedule_write(self, log_data: Dict[str, Any]) -> None:
        """
        把写数据库的任务提交到后台事件循环。

        run_coroutine_threadsafe：从同步代码安全地提交协程到另一个事件循环。
        """
        try:
            loop = _get_background_loop()
            future = asyncio.run_coroutine_threadsafe(
                self._write_to_db(log_data), loop
            )
            # 注册完成回调，如果写 DB 失败至少记录日志
            future.add_done_callback(self._on_write_done)
        except Exception as e:
            logger.warning("Failed to schedule LLM usage logging: %s", e)

    def _on_write_done(self, fut: Any) -> None:
        """写入完成后的回调。如果失败，记录错误日志但不抛出异常。"""
        try:
            fut.result()
        except Exception as e:
            logger.error("LLM usage write failed: %s", e)

    async def _write_to_db(self, log_data: Dict[str, Any]) -> None:
        """实际的数据库写入逻辑（在后台事件循环中执行）。"""
        try:
            from app.database.session import AsyncSessionLocal
            from app.database.usage_repository import UsageRepository

            async with AsyncSessionLocal() as session:
                repo = UsageRepository(session)
                await repo.create_log(log_data)
        except Exception as e:
            logger.error("Failed to write LLM usage log: %s", e)
            # 写 DB 失败时至少打一条结构化日志，保留数据
            logger.info(
                "LLM usage: module=%s model=%s input=%s output=%s total=%s duration=%sms",
                log_data.get("module_name"),
                log_data.get("model_name"),
                log_data.get("input_tokens"),
                log_data.get("output_tokens"),
                log_data.get("total_tokens"),
                log_data.get("duration_ms"),
            )


# ===== 全局单例 =====
# 整个应用只有一个回调处理器实例
_usage_callback = LLMUsageCallbackHandler()


def get_usage_callbacks() -> List[BaseCallbackHandler]:
    """
    获取 usage tracking 回调列表。
    每次创建 LLMInvoker 时调用此函数，把回调注入到 LLM 调用链中。
    """
    return [_usage_callback]
