"""
上下文管理模块。

这段代码是 context 包的入口文件。
作用是导出 ContextManager，供 ConversationService 和 AgentService 使用。

上下文管理解决什么问题？
  LLM 有 token 上限（qwen-max: 8192 tokens）。
  用户连续对话 20 轮后，历史消息可能超过 5000 tokens，
  加上系统提示词和 RAG 检索结果，很容易超出限制。
  上下文管理器负责在超出阈值前压缩历史对话。

用法：
  from app.context import context_manager

  ctx = await context_manager.build_context(
      messages=history,
      system_prompt=system_prompt,
      max_tokens=8000,
  )
"""

from app.context.manager import ContextManager, context_manager

__all__ = ["ContextManager", "context_manager"]
