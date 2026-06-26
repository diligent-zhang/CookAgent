"""
通用 Agent

处理 general_chat、cooking_help、ingredient_ask 等非菜谱检索意图。
核心流程：轻量 RAG 检索 + LLM 流式生成。

作用：处理 general_chat、cooking_help、ingredient_ask 三种意图。核心流程是
轻量 RAG 检索 + LLM 流式生成——不做精排，不做元数据过滤，这些轻量意图不需要
复杂的检索优化。

与 RecipeMasterAgent 的区别：
  - GeneralAgent: 不做精排，检索完直接 LLM 生成
  - RecipeMasterAgent: 元数据过滤 + 精排 + 增强 Prompt
"""
import logging
from typing import Callable, List, Optional

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from app.agent.agents.base import AgentContext, AgentResult, BaseAgent, Source
from app.llm.provider import LLMProvider
from app.rag.service import RAGService

logger = logging.getLogger(__name__)

GENERAL_AGENT_PROMPT = """你是CookHero，一个专业的烹饪助手。你的职责是帮助用户解决烹饪相关的问题。

## 能力范围
- 菜谱搜索和推荐
- 烹饪技巧和知识解答
- 食材信息查询（营养、搭配、保存、替代）
- 饮食建议

## 回答规则
1. 如果提供了菜谱资料，基于资料回答；资料中不包含的，诚实说明并给出通用建议
2. 回答清晰、步骤化，菜谱步骤按序号列出
3. 主动提示烹饪技巧、注意事项
4. 如果用户有饮食限制，优先考虑约束
5. 用中文回答，语气友好热情

你是用户厨房里的可靠伙伴！
"""


class GeneralAgent(BaseAgent):
    name = "general"
    description = "通用对话 Agent，处理烹饪技巧、食材问答和闲聊"
    intent_match = ["general_chat", "cooking_help", "ingredient_ask"]

    def __init__(self, llm_provider: LLMProvider, rag_service: RAGService):
        self.llm_provider = llm_provider
        self.rag_service = rag_service

    async def execute(
        self,
        context: AgentContext,
        stream_callback: Optional[Callable] = None,
    ) -> AgentResult:
        # 优先用改写后的查询，没改写就用原始查询
        query = context.rewritten_query or context.original_query

        # ==== RAG 检索 ====
        if stream_callback:
            await stream_callback("thinking", {"content": "正在检索相关资料..."})

        try:
            retrieved_docs = await self.rag_service.retrieve(
                query=query, user_id=context.user_id,
            )
        except Exception as e:
            logger.error("RAG retrieval failed: %s", e)
            retrieved_docs = []

        # ==== 构建消息（System Prompt + 历史 + 检索结果 + 用户消息）====
        messages = self._build_messages(
            query=query,
            retrieved_docs=retrieved_docs,
            compressed_summary=context.compressed_summary,
            recent_messages=context.recent_messages,
        )

        # ==== 发送检索来源给前端 ====
        if stream_callback:
            await stream_callback("sources", {
                "sources": self._build_sources(retrieved_docs),
            })

        # ==== 流式 LLM 生成 ====
        invoker = self.llm_provider.create_invoker("normal", streaming=True)
        full_response = ""

        try:
            async for chunk in invoker.astream(messages):
                token = self._extract_text(chunk)
                if token:
                    full_response += token
                    if stream_callback:
                        await stream_callback("token", {"content": token})
        except Exception as e:
            logger.error("LLM streaming failed: %s", e)
            error_msg = f"生成回答失败: {e}"
            if stream_callback:
                await stream_callback("error", {"content": error_msg})
            return AgentResult(content=error_msg)

        return AgentResult(
            content=full_response,
            sources=self._build_sources(retrieved_docs),
        )

    def _build_messages(self, query, retrieved_docs, compressed_summary, recent_messages):
        """构建完整的 LLM 消息列表。

        消息结构：
          [SystemMessage(角色设定),
           SystemMessage(对话摘要, 可选),
           ...历史消息...,
           HumanMessage(检索结果 + 用户问题)]
        """
        messages: list = [SystemMessage(content=GENERAL_AGENT_PROMPT)]

        # 如果有历史摘要（长对话触发压缩后），注入摘要
        if compressed_summary:
            messages.append(SystemMessage(content=f"对话历史摘要：{compressed_summary}"))

        # 最近 K 轮原文（和摘要互补：摘要覆盖全貌，原文保留细节）
        for msg in recent_messages:
            if hasattr(msg, "role") and hasattr(msg, "content"):
                if msg.role == "user":
                    messages.append(HumanMessage(content=msg.content))
                elif msg.role == "assistant":
                    messages.append(AIMessage(content=msg.content))

        # 检索结果作为上下文注入
        if retrieved_docs:
            parts = ["以下是从菜谱库中检索到的相关资料:\n"]
            for i, doc in enumerate(retrieved_docs, 1):
                name = doc.metadata.get("dish_name", "未知")
                parts.append(f"--- 资料{i}: {name} ---\n{doc.page_content}\n")
            messages.append(HumanMessage(
                content="\n".join(parts) + "\n\n请根据以上资料回答用户的问题。"
            ))

        messages.append(HumanMessage(content=query))
        return messages

    def _build_sources(self, docs) -> List[Source]:
        """从检索文档中提取来源信息（用于前端展示"参考来源"）。"""
        return [
            Source(
                dish_name=doc.metadata.get("dish_name", "未知"),
                category=doc.metadata.get("category", ""),
                relevance_score=doc.metadata.get("retrieval_score", 0.0),
            )
            for doc in docs
        ]

    def _extract_text(self, chunk) -> str:
        """从 LangChain 流式 chunk 中安全提取文本。"""
        if hasattr(chunk, "content") and isinstance(chunk.content, str):
            return chunk.content
        if hasattr(chunk, "message") and hasattr(chunk.message, "content"):
            return chunk.message.content or ""
        return ""
