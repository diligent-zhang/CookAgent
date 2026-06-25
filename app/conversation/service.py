"""
对话服务

核心流程（一次对话轮次）：
    1. 保存用户消息到 PostgreSQL
    2. 加载对话历史
    3. RAG 检索相关菜谱
    4. 构建 LLM 消息列表（system prompt + 历史 + 检索结果 + 用户消息）
    5. 流式调用 LLM
    6. 保存 AI 回答到 PostgreSQL
"""
import logging
from typing import AsyncIterator, List, Optional

from langchain_core.documents import Document
from langchain_core.messages import HumanMessage, SystemMessage
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.conversation_repository import ConversationRepository
from app.llm.context import llm_context
from app.llm.provider import LLMInvoker, LLMProvider
from app.rag.service import RAGService

logger = logging.getLogger(__name__)


def _orm_to_dict(obj) -> dict:
    """将 SQLAlchemy ORM 对象转为普通 dict，切断 session 依赖。"""
    return {
        c.name: getattr(obj, c.name)
        for c in obj.__table__.columns
    }

# System Prompt — 定义 AI 助手的角色和行为
SYSTEM_PROMPT = """
你是一个专业的烹饪助手 CookHero，你的职责是帮助用户解决烹饪相关的问题。

规则：
1. 回答基于提供的菜谱资料。如果菜谱中没有相关信息，诚实说明并给出通用建议。
2. 回答要清晰、步骤化。菜谱步骤按序号列出。
3. 主动提示烹饪技巧、注意事项和替代方案。
4. 如果用户有饮食限制或过敏，优先考虑这些约束。
5. 用中文回答，语气友好、热情。

你是用户厨房里的可靠伙伴！
"""


class ConversationService:
    """
    对话服务
    每次请求创建新实例，由 FastAPI 依赖注入 AsyncSession
    """

    def __init__(
        self,
        db: AsyncSession,
        llm_provider: LLMProvider,
        rag_service: RAGService,
    ):
        self.db = db
        self.llm_provider = llm_provider
        self.rag_service = rag_service
        self.repo = ConversationRepository(db)

    # ========== 对话管理 ==========

    async def create_conversation(
        self, user_id: str, title: str = "新对话"
    ):
        """创建新对话"""
        conv = await self.repo.create_conversation(user_id, title)
        return _orm_to_dict(conv)

    async def list_conversations(self, user_id: str):
        """列出用户的所有对话。"""
        convs = await self.repo.list_conversations(user_id)
        return [_orm_to_dict(c) for c in convs]

    async def get_conversation_detail(self, conv_id: str, user_id: str):
        """
        获取对话详情（含消息队列）
        做所有权校验：只能看自己的对话
        """
        conv = await self.repo.get_conversation(conv_id)
        if conv is None or conv.user_id != user_id:
            return None
        messages = await self.repo.get_messages(conv_id)
        result = _orm_to_dict(conv)
        result["messages"] = [_orm_to_dict(m) for m in messages]
        return result

    async def delete_conversation(self, conv_id: str, user_id: str) -> bool:
        """删除对话（做所有权校验）。"""
        conv = await self.repo.get_conversation(conv_id)
        if conv is None or conv.user_id != user_id:
            return False
        return await self.repo.delete_conversation(conv_id)

    # ========== 对话核心：流式聊天 ==========

    async def stream_chat(
        self,
        user_id: str,
        conv_id: str,
        user_message: str,
    ) -> AsyncIterator[dict]:
        """
        流式聊天的主流程。

        返回 SSE 事件 dict，每个 dict 包含：
          - {"type": "thinking", "content": "..."}     检索中
          - {"type": "sources", "sources": [...]}       检索到的菜谱
          - {"type": "token", "content": "..."}         逐 token 输出
          - {"type": "done", "message_id": "...", "total_tokens": 123}  完成
          - {"type": "error", "content": "..."}         出错

        参数：
            user_id: 当前用户 ID
            conv_id: 对话 ID
            user_message: 用户发送的消息文本
        """
        # ==== Step 1: 保存用户消息 ====
        try:
            await self.repo.add_message(conv_id, "user", user_message)
        except Exception as e:
            yield {"type": "error", "content": f"保存消息失败: {e}"}
            return

        # ==== Step 2: 加载对话历史 ====
        history = await self.repo.get_history_as_dicts(conv_id)

        # ==== Step 3: RAG 检索 ====
        yield {"type": "thinking", "content": "正在检索相关菜谱..."}
        try:
            retrieved_docs = await self.rag_service.retrieve(
                query=user_message,
                user_id=user_id,
            )
        except Exception as e:
            logger.error("RAG retrieval failed: %s", e)
            retrieved_docs = []

        # ==== Step 4: 构建 sources 信息发送给前端 ====
        sources = self._build_sources(retrieved_docs)
        if sources:
            yield {"type": "sources", "sources": sources}

        # ==== Step 5: 构建 LLM 消息列表 ====
        llm_messages = self._build_llm_messages(
            history=history,
            retrieved_docs=retrieved_docs,
            user_message=user_message,
        )

        # ==== Step 6: 流式调用 LLM ====
        invoker = self.llm_provider.create_invoker("normal", streaming=True)
        full_response = ""
        try:
            with llm_context(
                module_name="conversation",
                user_id=user_id,
                conversation_id=conv_id,
            ):
                async for chunk in invoker.astream(llm_messages):
                    token_text = self._extract_chunk_text(chunk)
                    if token_text:
                        full_response += token_text
                        yield {"type": "token", "content": token_text}
        except Exception as e:
            logger.error("LLM streaming failed: %s", e)
            yield {"type": "error", "content": f"生成回答失败: {e}"}
            return

        # ==== Step 7: 保存 AI 回答 ====
        if full_response:
            try:
                msg = await self.repo.add_message(
                    conv_id, "assistant", full_response,
                )
                yield {
                    "type": "done",
                    "message_id": msg.id,
                }
            except Exception as e:
                logger.error("Failed to save assistant message: %s", e)
                yield {"type": "done", "message_id": ""}
        else:
            yield {"type": "done", "message_id": ""}

    # ===== 私有方法 =====

    def _build_sources(self, docs: List[Document]) -> List[dict]:
        """从检索文档中提取来源信息。"""
        sources = []
        for doc in docs:
            sources.append({
                "dish_name": doc.metadata.get("dish_name", "未知"),
                "category": doc.metadata.get("category", ""),
                "source": doc.metadata.get("source", ""),
                "relevance_score": doc.metadata.get("retrieval_score", 0.0),
            })
        return sources

    def _build_llm_messages(
        self,
        history: List[dict],
        retrieved_docs: List[Document],
        user_message: str,
    ) -> list:
        """
        构建发给 LLM 的完整消息列表。

        结构：
          [SystemMessage, ...历史消息..., 上下文HumanMessage, 当前HumanMessage]

        上下文注入方式：在用户消息前面插入一条特殊的 HumanMessage，
        内容为检索到的菜谱文本。这样 LLM 就能"看到"这些资料。
        """
        messages = [SystemMessage(content=SYSTEM_PROMPT)]

        # 填入历史消息（排除刚才保存的这一条，避免重复）
        for msg in history[:-1]:
            if msg["role"] == "user":
                messages.append(HumanMessage(content=msg["content"]))
            elif msg["role"] == "assistant":
                from langchain_core.messages import AIMessage
                messages.append(AIMessage(content=msg["content"]))

        # 注入检索到的菜谱上下文（所有文档合并为一条消息）
        if retrieved_docs:
            context_parts = ["以下是从菜谱库中检索到的相关资料:\n"]
            for i, doc in enumerate(retrieved_docs, 1):
                dish_name = doc.metadata.get("dish_name", "未知菜品")
                context_parts.append(
                    f"--- 资料{i}: {dish_name} ---\n{doc.page_content}\n"
                )
            context_text = "\n".join(context_parts)
            messages.append(HumanMessage(
                content=f"{context_text}\n\n"
                        f"请根据以上资料回答用户的问题。如果资料中没有相关信息，"
                        f"请诚实说明，并基于你的烹饪知识给出通用建议。"
            ))

        # 当前用户消息
        messages.append(HumanMessage(content=user_message))
        return messages

    def _extract_chunk_text(self, chunk) -> str:
        """
        从 LangChain 流式 chunk 中提取文本增量。

        LangChain 流式 chunk 的结构：
          AIMessageChunk(content="番", ...)
          AIMessageChunk(content="茄", ...)
          AIMessageChunk(content="炒", ...)

        需要兼容不同版本 LangChain 的 chunk 格式。
        """
        if hasattr(chunk, "content") and isinstance(chunk.content, str):
            return chunk.content

        # 兼容某些版本把 content 放在 message 里
        if hasattr(chunk, "message") and hasattr(chunk.message, "content"):
            return chunk.message.content or ""
        return ""


#  流程图：

#   stream_chat("u123", "conv_456", "番茄炒蛋怎么做")
#   │
#   ├─ Step 1: 存用户消息 → PostgreSQL messages 表
#   │
#   ├─ Step 2: 加载历史 → [{"role":"user","content":"你好"}, ...]
#   │
#   ├─ Step 3: RAG 检索
#   │   └─ RAGService.retrieve("番茄炒蛋怎么做", "u123")
#   │       ├─ CacheManager.get() → 未命中
#   │       ├─ RetrievalModule.hybrid_search() → Milvus 混合检索
#   │       ├─ DocumentProcessor.post_process() → chunk→父文档
#   │       └─ CacheManager.set() → 写入缓存
#   │   → [Document("番茄炒蛋的做法..."), Document("番茄蛋花汤...")]
#   │
#   ├─ Step 4: 发送 sources 给前端（用于展示"参考来源"）
#   │
#   ├─ Step 5: 构建 LLM 消息
#   │   [SystemMessage("你是烹饪助手..."),
#   │    AIMessage("你好！"),           ← 历史
#   │    HumanMessage("你好"),          ← 历史
#   │    HumanMessage("以下是资料..."),  ← RAG 上下文
#   │    HumanMessage("番茄炒蛋怎么做")] ← 当前问题
#   │
#   ├─ Step 6: 流式调用 LLM
#   │   llm_context("conversation", "u123", "conv_456"):  ← 设置追踪上下文
#   │       invoker.astream(messages)
#   │           → yield {"type":"token","content":"番"}
#   │           → yield {"type":"token","content":"茄"}
#   │           → yield {"type":"token","content":"炒"}
#   │           ... 回调自动记录 token 用量到 llm_usage_logs 表
#   │
#   └─ Step 7: 保存 AI 回答 → PostgreSQL messages 表
#       → yield {"type":"done","message_id":"msg_789"}
