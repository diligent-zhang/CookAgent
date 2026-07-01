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
        redis_client=None,
    ):
        self.db = db
        self.llm_provider = llm_provider
        self.rag_service = rag_service
        self.repo = AgentSessionRepository(db)
        inject_rag_service(rag_service)

        # ===== P1 新增：意图识别和查询改写组件 =====
        # 【P1 修复】接入 Redis 做意图缓存
        # 之前传 redis_client=None，注释写"暂不接 Redis"。
        # 原因：AgentService 是每请求创建的，而 redis_client 是模块级全局变量，
        # 需要通过 get_agent_service() 传入。现在已贯通这条链路。
        # 效果：相同查询的意图识别直接走 Redis 缓存（~1ms），不再每次调 fast LLM（~300ms）
        self.intent_detector = IntentDetector(
            llm_provider=llm_provider,
            redis_client=redis_client,
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
          1. 加载历史消息（在存当前消息之前，避免重复）
          2. 保存用户消息
          3. 意图识别（IntentDetector, fast LLM ~200ms）
          4. 查询改写（QueryRewriter, fast LLM ~200ms）
          5. 构建 AgentContext
          6. AgentRegistry 路由 → 选择 Agent
          7. Agent.execute() → 流式返回
          8. 保存最终回答
        """
        # ==== Step 1: 加载历史（必须在保存当前消息之前）====
        # 为什么必须先加载历史再保存当前消息？
        #   如果先保存再加载，get_last_n_messages 会包含当前用户消息。
        #   后续 ReActAgent 和 GeneralAgent 构建上下文时会再把 original_query
        #   作为 HumanMessage 追加一次，导致当前问题出现两次（一次来自历史，
        #   一次来自显式追加），浪费上下文窗口。
        #   先加载历史则自然排除当前消息——和 conversation/service.py 里
        #   history[:-1] 的做法等价，但更简洁。
        history_msgs = await self.repo.get_last_n_messages(session_id, 20)

        # ==== Step 2: 保存用户消息 ====
        await self.repo.add_message(
            session_id=session_id, role="user",
            content=user_message, step_number=0,
        )

        # ==== Step 3: 意图识别 ====
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

        # ==== Step 4: 查询改写 ====
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

        # ==== Step 5: 构建 AgentContext ====
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

        # ==== Step 6: Agent 路由 ====
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

        # ════════════════════════════════════════════════════════════════════
        # ==== Step 7: 执行 Agent（P0 修复：真正的流式输出）====
        # ════════════════════════════════════════════════════════════════════
        #
        # 【修复前的架构问题】
        #   旧代码用 collect_events 回调把所有事件收集到一个 list 里，
        #   等 agent.execute() 完全结束后才一次性 yield 出去。
        #   这导致用户从发送消息到看到第一个字需要等待 5-15 秒——
        #   意图识别(~300ms) + 查询改写(~300ms) + ReAct 多轮循环(2-10s)。
        #   SSE 流式传输的格式虽然正确，但事件在一瞬间全部到达，
        #   前端也变成"瞬间刷出全部内容"，完全失去了流式体验。
        #
        # 【修复方案：asyncio.Queue 生产者-消费者模式】
        #                          ┌──────────────────┐
        #   agent.execute() ──→ stream_callback ──→ event_queue (asyncio.Queue)
        #   (在 background      (生产者：每次       (线程安全的异步队列)
        #    task 中运行)        Agent 产生事件
        #                       就 put 进队列)
        #
        #   主循环 (消费者) ←── event_queue.get() ←── 取出事件 → yield 给 SSE
        #   (在 stream_agent_chat 的 async generator 中运行)
        #
        #   关键：生产者(agent.execute) 和消费者(yield SSE) 现在并发运行！
        #   Agent 每产生一个 token/thought/tool_call，前端立刻就能看到，
        #   不再需要等待整个 ReAct 循环结束。
        #
        # 【为什么用 asyncio.Queue 而不是其他方案？】
        #   - asyncio.Queue 是标准库的异步安全队列，天生适合 async/await 场景
        #   - put() 和 get() 都是异步的，不会阻塞事件循环
        #   - 无需引入额外依赖（如 Redis Pub/Sub）
        #   - 在一个 asyncio event loop 内，队列操作几乎零延迟
        #
        # 【哨兵事件 __agent_result__ / __agent_error__】
        #   Agent 执行完成后，需要把 AgentResult 传回主循环做后处理
        #   （保存 DB、补发 sources 等）。用特殊 type 的哨兵事件来传递，
        #   避免引入第二个通信通道。前缀 __ 表明这是内部事件，不会 yield 给前端。
        #
        import asyncio

        # Agent 产生的事件队列（无界队列，内存中，请求结束即释放）
        event_queue: asyncio.Queue = asyncio.Queue()

        # 追踪 Agent 执行期间已经流式发送了哪些内容
        has_tokens = False    # 是否已经流式发送过 token 事件
        has_sources = False   # 是否已经流式发送过 sources 事件
        final_answer = ""     # 积累的最终回答文本（用于存 DB）

        async def stream_callback(event_type: str, data: dict):
            """
            Agent 内部事件的"生产者"。

            每当 Agent（ReActAgent / GeneralAgent / RecipeMasterAgent）
            产出一个事件（token、tool_call、observation、thought 等），
            就调用这个回调，回调立即把事件推入队列。

            这个函数在 agent.execute() 的上下文中被调用（同步/异步），
            但 put 到 asyncio.Queue 是异步安全的。
            """
            await event_queue.put({"type": event_type, **data})

        async def run_agent():
            """
            在后台 task 中执行 agent.execute()。

            为什么要包装一层？
            - agent.execute() 内部会调用 stream_callback 生产事件
            - 主循环需要同时消费这些事件
            - 用 asyncio.create_task 让 execute 在后台跑，主循环从队列中取
            - execute 完成后，用哨兵事件把 AgentResult 传给主循环

            异常处理：
            - execute 内部已经 try/catch 了大部分异常，这里兜底捕获
            - 异常通过 __agent_error__ 哨兵传给主循环，由主循环 yield error 事件
            """
            try:
                result = await agent.execute(context, stream_callback=stream_callback)
                # Agent 正常完成 → 用哨兵事件传递 AgentResult
                await event_queue.put({"type": "__agent_result__", "result": result})
            except Exception as e:
                logger.error("Agent execution failed: %s", e)
                await event_queue.put({"type": "__agent_error__", "content": str(e)})

        # 启动后台 task：Agent 开始执行，事件开始流入队列
        agent_task = asyncio.create_task(run_agent())

        # ── 主循环：消费队列中的事件，实时 yield 给 SSE ──
        result = None
        while True:
            # 阻塞等待下一个事件（无论是 Agent 产生的，还是哨兵）
            event = await event_queue.get()
            event_type = event.get("type", "")

            if event_type == "__agent_result__":
                # 【哨兵】Agent 正常执行完毕，取出 AgentResult 用于后处理
                result = event.get("result")
                break  # 退出消费循环，进入后处理阶段

            elif event_type == "__agent_error__":
                # 【哨兵】Agent 执行过程中抛出了未捕获的异常
                yield {"type": "error", "content": f"Agent 执行失败: {event.get('content', '')}"}
                return  # 直接结束，不保存回答

            elif event_type == "token":
                # LLM 产生的逐字输出 → 标记，积累，立即发送给前端
                has_tokens = True
                final_answer += event.get("content", "")
                yield event

            elif event_type == "done":
                # Agent 内部（ReActAgent.run()）发出的 done 事件
                # 包含完整的 final_answer 文本（和 token 累加的结果相同）
                # 注意：这个 done 不直接发给前端！
                #   因为 Step 7 还需要把回答存到 DB 后才能发最终的 done
                #   这里只捕获 content，等后处理完再由我们发外层 done
                final_answer = event.get("content", "") or final_answer

            elif event_type == "sources":
                # 检索结果来源 → 标记，发送给前端展示"参考来源"
                has_sources = True
                yield event

            elif event_type == "error":
                # Agent 内部的可恢复错误 → 直接转发给前端
                yield event

            else:
                # 其他事件类型（thought, tool_call, observation, thinking 等）
                # → 直接透传给前端，不做额外处理
                yield event

        # ── 消费循环结束，确保后台 task 完全结束 ──
        # 正常情况下循环是被 __agent_result__ 哨兵 break 的，
        # 此时 agent_task 已经 done。这里 await 只是确保无异常残留。
        try:
            if not agent_task.done():
                await agent_task
        except Exception:
            pass  # 异常已通过 __agent_error__ 处理，这里忽略

        # ── 后处理：补发 Agent 未流式发送的内容 ──
        # 不同的 Agent（GeneralAgent vs RecipeMasterAgent）实现方式不同：
        # - GeneralAgent 通过 stream_callback 发了 sources 和 token
        # - RecipeMasterAgent 通过 ReActAgent 发了 token，但 sources 可能只在 AgentResult 中
        # 这里统一兜底：如果没通过 stream 发过，就从 AgentResult 中补发

        if result and result.sources and not has_sources:
            # Agent 有检索来源但没通过 stream_callback 发过 → 补发
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

        if not has_tokens and result and result.content:
            # Agent 有回答内容但没通过 stream_callback 发过 token
            # （极端情况：Agent 直接返回结果，没有逐 token 流式输出）
            final_answer = result.content
            yield {"type": "token", "content": result.content}

        # ════════════════════════════════════════════════════════════════════
        # ==== Step 8: 保存回答 + 更新标题 ====
        # ════════════════════════════════════════════════════════════════════
        # 为什么保存要放在流式输出之后而不是之前？
        #   流式输出的目标是让用户尽快看到内容，DB 写入是"事后存档"。
        #   如果保存失败（极少发生），回答已经展示给用户了，不影响体验。
        if final_answer:
            await self.repo.add_message(
                session_id=session_id, role="assistant",
                content=final_answer, step_number=99,
            )
            # 用用户消息的前 20 个字作为会话标题
            title = user_message[:20] + ("..." if len(user_message) > 20 else "")
            await self.repo.update_session_title(session_id, title)

        yield {"type": "done", "content": final_answer}
