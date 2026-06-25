#   这是 LLM 调用层的核心，封装了两样东西：
#   - LLMProvider：负责根据配置创建 ChatOpenAI 实例
#   - LLMInvoker：封装调用逻辑，每次调用随机选模型做简单负载均衡

#   """
#   LLM Provider - 统一的 LLM 初始化和调用入口

#   核心概念:
#   1. LLMProvider - 全局 LLM 提供者，管理配置和创建 ChatOpenAI 实例
#   2. LLMInvoker - LLM 调用器，封装调用逻辑，支持随机模型选择和 tool
#   calling
#   """

import random
from typing import Any, AsyncIterator
from langchain_openai import ChatOpenAI
from app.config import settings
from app.llm.callbacks import get_usage_callbacks

class LLMProvider:
    """
    LLM提供者。
    负责根据配置创建ChatOpenAI实例
    支持fast和normal两种模型层级
    """
    def __init__(self):
        #直接从全局settings读取LLm配置
        self._config = settings.llm
    
    def get_profile(self,llm_type:str | None = None):
        """
        获取指定类型的LLM配置。默认返回normal。
        """ 
        return self._config.get_profile(llm_type)
    
    def pick_model(self,llm_type: str | None = None) ->str:
        """
        随机选择一个模型名称。
        每次调用从model_names列表中随机选一个，实现简单的负载均衡
        """
        profile = self.get_profile(llm_type)
        if not profile.model_names:
            raise ValueError("model_names cannot be empty")
        return random.choice(profile.model_names)
    def create_llm(
            self,
            llm_type:str | None = None,
            *,
            streaming: bool = False,
            temperature: float | None = None,
            max_tokens: int | None = None,
            **kwargs,
    ) -> ChatOpenAI:
        """
        创建ChatOpenAI实例。
        所有LLM调用都走阿里云DashScope（OpenAI兼容API）
        """
        profile = self.get_profile(llm_type)

        return ChatOpenAI(
            model=profile.pick_default_model(),
            api_key=settings.llm_api_key,
            base_url=profile.base_url,
            temperature=(
                temperature if temperature is not None else profile.temperature
            ),
            max_completion_tokens=(
                max_tokens if max_tokens is not None else profile.max_tokens
            ),
            streaming=streaming,
            stream_usage=True,  # 流式模式下也返回 token 用量
            callbacks=get_usage_callbacks(),  # 自动追踪 LLM token 用量
            **kwargs,
        )
    def create_invoker(
          self,
          llm_type: str | None = None,
          *,
          streaming: bool = False,
          **kwargs,
      ) -> "LLMInvoker":
          """
          创建带调用逻辑封装的 LLMInvoker。
          推荐使用此方法而非直接使用 create_llm()。
          """
          base_llm = self.create_llm(llm_type, streaming=streaming,**kwargs)
          return LLMInvoker(
              provider=self,
              llm_type=llm_type,
              base_llm=base_llm,
          )

class LLMInvoker:
      """
      LLM 调用器。
      封装调用逻辑：
      - 每次调用随机选模型（负载均衡）
      - 支持 tool calling
      - 支持流式输出
      """

      def __init__(
          self,
          provider: LLMProvider,
          llm_type: str | None,
          base_llm: ChatOpenAI,
      ):
          self._provider = provider
          self._llm_type = llm_type
          self._base_llm = base_llm

      def _get_llm_with_model(self, tools: list | None = None) ->ChatOpenAI:
          """
          获取绑定了随机模型的 LLM 实例。
          每次调用随机选模型，实现简单的负载均衡。
          """
          model = self._provider.pick_model(self._llm_type)
          llm = self._base_llm.bind(model=model)

          if tools:
              llm = llm.bind(tools=tools)

          return llm

      async def ainvoke(self, messages: list, **kwargs) -> Any:
          """异步非流式调用 LLM。"""
          return await self._get_llm_with_model().ainvoke(messages,**kwargs)
      async def ainvoke_with_tools(
          self, messages: list, tools: list, **kwargs
      ) -> Any:
          """异步非流式调用 LLM（带 tool schemas，用于 Agent）。"""
          return await self._get_llm_with_model(tools=tools).ainvoke(
              messages, **kwargs
          )

      async def astream(self, messages: list, **kwargs) ->AsyncIterator[Any]:
          """异步流式调用 LLM（逐 token 输出）。"""
          async for chunk in self._get_llm_with_model().astream(messages,
   **kwargs):
              yield chunk

      async def astream_with_tools(
          self, messages: list, tools: list, **kwargs
      ) -> AsyncIterator[Any]:
          """异步流式调用 LLM（带 tool schemas）。"""
          llm = self._get_llm_with_model(tools=tools)
          async for chunk in llm.astream(messages, **kwargs):
              yield chunk



#                 模块解释

#   LLMProvider.create_invoker("fast")
#     → 读取 fast profile 配置（qwen-plus / 0.7 / 8192）
#     → 创建 ChatOpenAI 实例（LangChain 提供的 OpenAI 兼容客户端）
#     → 包装为 LLMInvoker

#   LLMInvoker.ainvoke(messages)
#     → pick_model() 从 ["qwen-plus"] 随机选一个模型
#     → ChatOpenAI.bind(model="qwen-plus").ainvoke(messages)
#     → 返回 AI 响应

#   为什么每次调用都 bind(model=...)？
#     — 如果 model_names 里有多个模型，随机选可以实现负载均衡
#     — ChatOpenAI 本身不提供这种能力，所以我们自己封装

#   stream_usage=True：LangChain 的 ChatOpenAI 在流式模式下默认不返回 token
#    用量，设为 True 后在流式 chunk 的最后一个会带上 usage
#   信息（后续阶段3.3 的 usage tracking 依赖它）。
