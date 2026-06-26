#作用：在RAG检索之前优化用户查询。用户的口语表达（“上次那个排骨怎么做来着”）
#和菜谱文档中的书面表达（“排骨做法”）在向量空间中距离可能很远。改写器用fsat LLM去掉噪音词
#、补全关键词，让检索更精准。
#触发条件：只在 recipe_search 和 cooking_help 意图时触发，闲聊不需要检索所以不触发。
"""
查询改写器
  在 RAG 检索之前优化用户查询，提升检索命中率。
  使用 fast LLM 做改写，仅对特定意图触发。

  设计决策：
    - 为什么只在部分意图触发？闲聊和饮食规划不需要检索菜谱，改写没意义
    - 为什么用 fast LLM 而不是 normal？改写是轻量任务，200ms 内完成，用 fast 省钱
    - 为什么改写失败返回原查询？降级策略，不阻断主流程
"""
import logging
from typing import Optional
from app.conversation.prompts import QUERY_REWRITE_PROMPT
from app.llm.provider import LLMProvider
logger = logging.getLogger(__name__)

#需要改写的意图类型（闲聊和饮食规划不需要检索优化）
REWRITE_INTENTS = {"recipe_search","cooking_help"}

class QueryRewriter:
    def __init__(
            self,
            llm_provider:LLMProvider,
            enabled:bool = True,
            max_length:int = 200,
    ):
        self.llm_provider = llm_provider
        self.enabled = enabled
        self.max_length = max_length
    async def rewrite(self, query:str,intent_type: str)->str:
        """
        改写用户查询。

          三级保护，任何一级不满足都返回原查询：
            1. enabled = False → 不启用
            2. 意图类型不在白名单 → 不需要改写
            3. 查询太短 (<10字) → 没有噪音可去除
            4. LLM 出错 → 降级返回原查询
        """
        if not self.enabled:
            return query
        if intent_type not in REWRITE_INTENTS:
            return query
        if len(query)<20:
            return query
        
        try:
            rewritten = await self._call_llm(query)
            if rewritten and len(rewritten)<=self.max_length:
                logger.info(
                    "Query rewritten:'%s...'--'%s...'",
                    query[:30],rewritten[:30],
                )
                return rewritten
            else:
                return query
        except Exception as e:
            logger.warning("Query rewriting failed: %s, using original",e)
            return query
        
    async def _call_llm(self,query: str)-> str:
        """调用fast LLM改写查询。"""
        from langchain_core.messages import HumanMessage,SystemMessage
        invoker = self.llm_provider.create_invoker("fast",streaming=False)
        messages = [
            SystemMessage(content=QUERY_REWRITE_PROMPT),
            HumanMessage(content=query),
        ]
        response = await invoker.ainvoke(messages)
        if hasattr(response,"content"):
            return response.content.strip()
        return str(response).strip()
