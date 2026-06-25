"""
Compressor — LLM 对话摘要压缩器。

这段代码使用 fast LLM（qwen-plus）将长对话历史压缩为简短摘要。

技术要点：
  - 使用 fast LLM 而非 normal LLM：
    压缩是辅助任务，不需要高质量输出，qwen-plus 足够且更快（~300ms vs ~800ms）
  - 增量压缩策略：
    首次：history[0:10] → summary_v1
    后续：summary_v1 + history[10:15] → summary_v2
    增量比全量便宜（输入 token 更少）
  - 压缩提示词：
    要求 LLM 提取关键信息（用户偏好、过敏食材、对话主题），
    忽略闲聊和问候语。这样摘要既小又有实用价值。
"""

import logging
from typing import List, Optional

logger = logging.getLogger(__name__)

# 压缩提示词——指导 LLM 如何做摘要。
# 为什么用中文？因为对话内容主要是中文，中文提示词效果更好。
COMPRESSION_SYSTEM_PROMPT = """你是一个对话摘要助手。请将以下对话历史压缩为简洁的摘要。

摘要规则：
1. 保留用户的关键信息：饮食偏好、过敏食材、口味喜好、烹饪设备
2. 保留讨论过的菜谱名称和用户的评价
3. 保留用户提出的具体要求（如"低脂""快手""适合新手"）
4. 忽略问候语、闲聊和重复内容
5. 使用中文，控制在 300 字以内

输出格式：直接输出摘要文本，不需要标题或前缀。"""


class Compressor:
    """
    对话摘要压缩器。

    为什么不用更简单的"只保留最近 N 轮"截断策略？
      截断会丢失早期关键信息。例如用户在第 3 轮说"我对花生过敏"，
      如果截断了前 5 轮，LLM 会在不知道过敏的情况下推荐宫保鸡丁。
      摘要可以保留这种关键信息，同时大幅减少 token 占用。

    使用方式：
      compressor = Compressor()
      summary = await compressor.compress(old_messages)
    """

    def __init__(self):
        self._llm_provider = None

    def _get_llm(self):
        """
        获取 fast LLM provider。

        懒加载的原因：Compressor 实例化时 LLMProvider 可能还没创建。
        第一次实际调用 compress 时才获取，此时 LLMProvider 一定已初始化。
        """
        if self._llm_provider is None:
            from app.llm.provider import LLMProvider
            self._llm_provider = LLMProvider()
        return self._llm_provider

    def _messages_to_text(self, messages: List) -> str:
        """
        将消息对象列表转换为纯文本。

        输出格式：
          用户: xxx
          助手: xxx

        为什么转文本而不是传标准的 messages 格式？
          LLM 的 compress 调用只发一条 HumanMessage，里面包含全部历史文本。
          这样可以省去多轮 messages 的结构开销，输入更紧凑。
        """
        lines = []
        for msg in messages:
            role = getattr(msg, 'role', 'user')
            content = getattr(msg, 'content', '') or ''
            if role in ('user', 'human'):
                lines.append(f"用户: {content}")
            elif role in ('assistant', 'ai', 'bot'):
                lines.append(f"助手: {content}")
        return "\n".join(lines)

    async def compress(
        self,
        old_messages: List,
        previous_summary: Optional[str] = None,
    ) -> Optional[str]:
        """
        将旧消息列表压缩为摘要。

        参数：
          old_messages: 需要压缩的历史消息
          previous_summary: 之前的摘要（增量压缩时传入）

        返回值：
          压缩后的摘要文本，或 None（压缩失败时降级返回部分原文）

        增量逻辑：
          - 有 previous_summary → 增量模式：输入 = "旧摘要 + 新消息"
          - 无 previous_summary → 首次压缩：输入 = 全部历史
        """
        if not old_messages:
            return previous_summary

        history_text = self._messages_to_text(old_messages)

        # 构建压缩输入文本
        if previous_summary:
            compress_input = (
                f"之前的对话摘要：\n{previous_summary}\n\n"
                f"新的对话内容：\n{history_text}\n\n"
                f"请将上述内容合并更新为一份完整的摘要。"
            )
        else:
            compress_input = (
                f"对话内容：\n{history_text}\n\n"
                f"请为以上对话生成摘要。"
            )

        # 输入太短就不压缩了，直接返回原文
        if len(compress_input) < 200:
            return history_text[:500]

        try:
            provider = self._get_llm()

            # 使用 LLMInvoker 而非直接 new ChatOpenAI，
            # 这样走统一的创建路径，自动带上 usage callbacks
            invoker = provider.create_invoker(
                "fast",
                temperature=0.3,      # 低温度：摘要需要准确，不要创意
                max_tokens=500,       # 摘要 500 tokens 足够
            )

            from langchain_core.messages import SystemMessage, HumanMessage

            response = await invoker.ainvoke([
                SystemMessage(content=COMPRESSION_SYSTEM_PROMPT),
                HumanMessage(content=compress_input),
            ])

            # response 是 AIMessage，.content 获取文本
            summary = response.content.strip()
            if summary:
                logger.info(
                    f"Compression: {len(old_messages)} messages → "
                    f"{len(summary)} chars summary"
                )
                return summary
            return None

        except Exception as e:
            # 压缩失败降级为截断，不影响对话流程
            logger.warning(f"Compression failed: {e}, falling back to truncation")
            if len(history_text) > 1000:
                return history_text[-1000:] + "\n...(已截断)"
            return history_text
