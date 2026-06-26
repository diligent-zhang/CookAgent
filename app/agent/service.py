"""
Agent 服务（P1 重构版）

编排 Agent 对话的完整生命周期：
1. 意图识别 → 选择 Agent
2. 查询改写 → 提升检索质量
3. 构建 AgentContext → 统一上下文传递
4. AgentRegistry 路由 → 选择合适的 Agent 执行
5. 流式返回 SSE → 前端展示

P0 流程: 保存消息 → 加载历史 → 创建 ReActAgent → 执行
P1 流程: 保存消息 → 意图识别 → 查询改写 → Registry 路由 → Agent 执行

P1 多了两步（意图识别 + 查询改写），但总延迟更低：
  - 意图识别: fast LLM ~200ms
  - 查询改写: fast LLM ~200ms
  - Agent 执行: ~2s（带意图引导，减少无效工具调用）
"""
import logging
from typing import Any, AsyncIterator, Dict, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.agents.base import AgentContext
from app.agent.registry.hub import get_agent_registry
from app.agent.session_repo import AgentSessionRepository
from app.agent.tools import inject_rag_service
from app.conversation.intent import IntentDetector
from app.conversation.query_rewriter import QueryRewriter
from app.config import settings
from app.llm.provider import LLMProvider
from app.rag.service import RAGService

logger = logging.getLogger(__name__)


def _orm_to_dict(obj) -> dict:
    """将 SQLAlchemy ORM 对象转为普通 dict，切断 session 依赖。"""
    return {
        c.name: getattr(obj, c.name)
        for c in obj.__table__.columns
    }


class AgentService:
    """
    Agent 服务（P1 重构版）。

    每次请求创建新实例，由 FastAPI 依赖注入 AsyncSession。
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
        self.repo = AgentSessionRepository(db)
        inject_rag_service(rag_service)

        # ===== P1 新增：意图识别和查询改写组件 =====
        self.intent_detector = IntentDetector(
            llm_provider=llm_provider,
            redis_client=None,  # Agent 模块暂不接 Redis 做意图缓存
            cache_ttl=settings.intent_cache_ttl,
        )
        self.query_rewriter = QueryRewriter(
            llm_provider=llm_provider,
            enabled=settings.query_rewriting_enabled,
            max_length=settings.query_rewrite_max_length,
        )

    async def create_session(self, user_id: str, title: str = "新对话"):
        """创建 Agent 会话"""
        return await self.repo.create_session(user_id, title)

    async def list_sessions(self, user_id: str):
        """列出用户的所有 Agent 会话。"""
        sessions = await self.repo.list_sessions(user_id)
        return [_orm_to_dict(s) for s in sessions]

    async def get_session_detail(self, session_id: str, user_id: str):
        """获取会话详情。"""
        sess = await self.repo.get_session(session_id)
        if sess is None or sess.user_id != user_id:
            return None
        messages = await self.repo.get_messages(session_id)
        result = _orm_to_dict(sess)
        result["messages"] = [_orm_to_dict(m) for m in messages]
        return result

    async def delete_session(self, session_id: str, user_id: str) -> bool:
        """删除会话。"""
        sess = await self.repo.get_session(session_id)
        if sess is None or sess.user_id != user_id:
            return False
        return await self.repo.delete_session(session_id)

    async def stream_agent_chat(
        self,
        user_id: str,
        session_id: str,
        user_message: str,
    ) -> AsyncIterator[Dict[str, Any]]:
        """
        P1 版 Agent 聊天流程。

        流程：
          1. 保存用户消息
          2. 意图识别（IntentDetector, fast LLM ~200ms）
          3. 查询改写（QueryRewriter, fast LLM ~200ms）
          4. 构建 AgentContext
          5. AgentRegistry 路由 → 选择 Agent
          6. Agent.execute() → 流式返回
          7. 保存最终回答
        """
        # ==== Step 1: 保存用户消息 ====
        await self.repo.add_message(
            session_id=session_id, role="user",
            content=user_message, step_number=0,
        )

        # ==== Step 2: 意图识别 ====
        yield {"type": "thought", "content": "正在理解你的需求..."}
        try:
            intent = await self.intent_detector.detect(user_message)
        except Exception as e:
            logger.warning("Intent detection failed: %s, using fallback", e)
            from app.conversation.intent import Intent, IntentFilters
            intent = Intent(
                type=settings.intent_fallback,
                confidence=0.3,
                keywords=[user_message],
                filters=IntentFilters(),
            )

        yield {
            "type": "intent",
            "intent_type": intent.type,
            "confidence": intent.confidence,
        }

        # ==== Step 3: 查询改写 ====
        rewritten_query = user_message
        if settings.query_rewriting_enabled:
            try:
                rewritten_query = await self.query_rewriter.rewrite(
                    query=user_message, intent_type=intent.type,
                )
            except Exception as e:
                logger.warning("Query rewriting failed: %s", e)

        if rewritten_query != user_message:
            yield {"type": "thought", "content": f"优化查询: {rewritten_query}"}

        # ==== Step 4: 构建 AgentContext ====
        history_msgs = await self.repo.get_last_n_messages(session_id, 20)

        context = AgentContext(
            user_id=user_id,
            session_id=session_id,
            intent_type=intent.type,
            intent_confidence=intent.confidence,
            rewritten_query=rewritten_query,
            original_query=user_message,
            compressed_summary=None,
            recent_messages=history_msgs,
        )

        # ==== Step 5: Agent 路由 ====
        try:
            registry = get_agent_registry()
            agent = registry.match(intent.type)
            logger.info(
                "Routed intent='%s' → agent='%s'", intent.type, agent.name,
            )
        except Exception as e:
            logger.error("Agent routing failed: %s", e)
            yield {"type": "error", "content": f"Agent 路由失败: {e}"}
            return

        # ==== Step 6: 执行 Agent ====
        collected_events = []

        async def collect_events(event_type: str, data: dict):
            collected_events.append({"type": event_type, **data})

        try:
            result = await agent.execute(context, stream_callback=collect_events)

            has_tokens = any(e["type"] == "token" for e in collected_events)

            for event in collected_events:
                yield event

            if not has_tokens and result.content:
                yield {"type": "token", "content": result.content}

            if result.sources and not any(
                e["type"] == "sources" for e in collected_events
            ):
                yield {
                    "type": "sources",
                    "sources": [
                        {
                            "dish_name": s.dish_name,
                            "category": s.category,
                            "relevance_score": s.relevance_score,
                        }
                        for s in result.sources
                    ],
                }

            final_answer = result.content

        except Exception as e:
            logger.error("Agent execution failed: %s", e)
            yield {"type": "error", "content": f"Agent 执行失败: {e}"}
            return

        # ==== Step 7: 保存回答 + 更新标题 ====
        if final_answer:
            await self.repo.add_message(
                session_id=session_id, role="assistant",
                content=final_answer, step_number=99,
            )
            title = user_message[:20] + ("..." if len(user_message) > 20 else "")
            await self.repo.update_session_title(session_id, title)

        yield {"type": "done", "content": final_answer}
