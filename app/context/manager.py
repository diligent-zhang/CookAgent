"""
ContextManager — 对话上下文管理。

这段代码实现了滑动窗口 + 摘要压缩的混合策略。

工作流程：
  1. 计算所有历史消息的总 token 估算值
  2. 如果总 token < 阈值（max_tokens * 60%），直接返回全部历史
  3. 如果超出阈值：
     a. 保留最近 K 轮对话的原文（K 由配置决定，默认 5 轮）
     b. 较早的消息交给 Compressor 生成摘要
     c. 最终上下文 = system_prompt + 摘要 + 最近消息 + 当前消息

为什么是 60% 阈值而不是 100%？
  LLM 生成回答本身也需要 token 空间（max_tokens 参数）。
  如果上下文已经占满 8192 tokens，LLM 就没有空间生成回答了。
  60% 给上下文，40% 留给生成和 RAG 结果。

Token 估算方式：
  中文：约 1.5 字符 / token，英文：约 4 字符 / token
  取保守值 2 字符/token，高估 token 数（更早触发压缩，更安全）。
  对于 qwen 系列模型，没有公开的 tokenizer 可用，这是务实的折中。
"""

import logging
from typing import List

logger = logging.getLogger(__name__)


class ContextManager:
    """
    对话上下文管理器。

    配置来源（config.yml → context 段，第 4 批添加）：
      trigger_turns:     10    # 超过 10 轮触发首次压缩
      incremental_turns: 5     # 后续每 5 轮增量压缩
      recent_keep_turns: 5     # 保留最近 5 轮原文
      reserve_ratio:     0.6   # 上下文使用 max_tokens 的 60%
    """

    # token 估算系数：保守估计 ~2 字符 = 1 token
    CHARS_PER_TOKEN = 2

    def __init__(self):
        # 从配置读取参数，提供合理默认值
        from app.config import settings
        ctx_cfg = getattr(settings, 'context', None)
        if ctx_cfg:
            self.trigger_turns = ctx_cfg.trigger_turns
            self.incremental_turns = ctx_cfg.incremental_turns
            self.recent_keep_turns = ctx_cfg.recent_keep_turns
            self.reserve_ratio = ctx_cfg.reserve_ratio
        else:
            # 配置还没加时的默认值，保证不崩
            self.trigger_turns = 10
            self.incremental_turns = 5
            self.recent_keep_turns = 5
            self.reserve_ratio = 0.6

        self.compressor = None  # 懒加载，避免循环导入

    def _get_compressor(self):
        """
        懒加载 Compressor。

        不在 __init__ 里直接导入的原因：
          Compressor 依赖 LLMProvider，而 LLMProvider 的初始化顺序
          可能在 ContextManager 之后。懒加载在第一次实际需要压缩时才
          创建 Compressor，此时 LLMProvider 一定已经就绪。
        """
        if self.compressor is None:
            from app.context.compress import Compressor
            self.compressor = Compressor()
        return self.compressor

    def estimate_tokens(self, text: str) -> int:
        """
        估算文本的 token 数量。

        用字符数粗略估算。2 字符/token 对中文偏保守（实际约 1.5），
        意味着会在实际 token 上限之前触发压缩，更安全。
        """
        if not text:
            return 0
        return max(1, len(text) // self.CHARS_PER_TOKEN)

    def _count_turns(self, messages: List) -> int:
        """
        统计对话轮数。

        一轮 = user message + assistant response。
        用轮数比用消息数更直观——"聊了 10 个来回了，该压缩了"。
        """
        turns = 0
        for msg in messages:
            role = getattr(msg, 'role', '')
            if role in ('user', 'human'):
                turns += 1
        return turns

    async def build_context(
        self,
        messages: List,
        system_prompt: str = "",
        max_tokens: int = 8192,
        force_compress: bool = False,
    ) -> dict:
        """
        构建发送给 LLM 的最终上下文。

        参数：
          messages: 历史消息列表（对象需要有 role 和 content 属性）
          system_prompt: 系统提示词
          max_tokens: LLM 上下文窗口大小
          force_compress: 强制压缩（Agent 模式下的中间步骤即使没超阈值也压缩）

        返回值：
          {
            "system_prompt": str,
            "compressed_summary": Optional[str],
            "recent_messages": List,
            "total_estimated_tokens": int,
            "was_compressed": bool,
          }

        调用方拿到返回的 dict 后，用 format_for_llm() 拼装最终的 messages 列表。
        """
        # ---- 第 1 步：计算各部分的 token 估算值 ----
        system_tokens = self.estimate_tokens(system_prompt)

        # 分离"当前问题"和"历史"
        current_msg = None
        history = list(messages)

        if history:
            for i in range(len(history) - 1, -1, -1):
                role = getattr(history[i], 'role', '')
                if role in ('user', 'human'):
                    current_msg = history[i]
                    history = history[:i]
                    break

        # 如果没找到 user 消息（全部是 assistant），不做分离
        if current_msg is None and history:
            current_msg = history[-1]
            history = history[:-1]

        # ---- 第 2 步：估算 token 数 ----
        history_text = "".join(
            getattr(m, 'content', '') or '' for m in history
        )
        history_tokens = self.estimate_tokens(history_text)
        current_text = getattr(current_msg, 'content', '') if current_msg else ""
        current_tokens = self.estimate_tokens(current_text)

        # ---- 第 3 步：计算可用空间 ----
        available = int(max_tokens * self.reserve_ratio)

        # ---- 第 4 步：判断是否需要压缩 ----
        total_without_generation = system_tokens + history_tokens + current_tokens
        turns = self._count_turns(history)

        should_compress = (
            force_compress
            or total_without_generation > available
            or turns >= self.trigger_turns
        )

        if not should_compress:
            return {
                "system_prompt": system_prompt,
                "compressed_summary": None,
                "recent_messages": history + ([current_msg] if current_msg else []),
                "total_estimated_tokens": total_without_generation,
                "was_compressed": False,
            }

        # ---- 第 5 步：执行压缩 ----
        keep_count = self.recent_keep_turns * 2  # N 轮 = 2N 条消息

        if len(history) <= keep_count:
            return {
                "system_prompt": system_prompt,
                "compressed_summary": None,
                "recent_messages": history + ([current_msg] if current_msg else []),
                "total_estimated_tokens": total_without_generation,
                "was_compressed": False,
            }

        old_messages = history[:-keep_count]
        recent_messages = history[-keep_count:]

        compressor = self._get_compressor()
        summary = await compressor.compress(old_messages)

        # ---- 第 6 步：计算压缩后的统计 ----
        recent_text = "".join(
            getattr(m, 'content', '') or '' for m in recent_messages
        )
        summary_tokens = self.estimate_tokens(summary) if summary else 0
        compressed_total = (
            system_tokens
            + summary_tokens
            + self.estimate_tokens(recent_text)
            + current_tokens
        )

        logger.info(
            f"Context compressed: {len(old_messages)} messages → "
            f"{summary_tokens} tokens summary, "
            f"total now ~{compressed_total} tokens"
        )

        return {
            "system_prompt": system_prompt,
            "compressed_summary": summary,
            "recent_messages": recent_messages + ([current_msg] if current_msg else []),
            "total_estimated_tokens": compressed_total,
            "was_compressed": True,
        }

    def format_for_llm(self, context: dict) -> List[dict]:
        """
        将 build_context 的返回结果格式化为 LLM 可用的 messages 列表。

        这段函数是 build_context 的配套——把结构化的上下文字典转成
        OpenAI 兼容的 messages 格式。

        用法：
          ctx = await context_manager.build_context(messages, prompt)
          llm_messages = context_manager.format_for_llm(ctx)
          response = await llm.ainvoke(llm_messages)
        """
        formatted = []

        # 系统提示词（合并压缩摘要）
        system_text = context["system_prompt"]
        if context.get("compressed_summary"):
            system_text += (
                f"\n\n[对话历史摘要]\n"
                f"以下是与用户之前的对话摘要，请基于这些背景信息回答问题：\n"
                f"{context['compressed_summary']}"
            )
        formatted.append({"role": "system", "content": system_text})

        # 最近消息原文
        for msg in context.get("recent_messages", []):
            role = getattr(msg, 'role', 'user')
            content = getattr(msg, 'content', '')
            if role in ('user', 'human'):
                formatted.append({"role": "user", "content": content})
            elif role in ('assistant', 'ai', 'bot'):
                formatted.append({"role": "assistant", "content": content})
            elif role == 'system':
                formatted.append({"role": "system", "content": content})

        return formatted


# 全局单例 — 模块导入时即创建，无状态管理器
context_manager = ContextManager()
