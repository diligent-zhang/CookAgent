"""
Agent 服务

编排 Agent 对话的完整生命周期：
1. 创建/获取 Agent 会话
2. 构建上下文信息（含历史）
3. 启动 ReAct Agent
4. 保存所有步骤到数据库
5. 流式返回 SSE 事件
"""
import logging
from typing import Any, AsyncIterator, Dict, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.agent import ReActAgent
from app.agent.session_repo import AgentSessionRepository
from app.agent.tools import get_all_tools, inject_rag_service
from app.llm.provider import LLMInvoker, LLMProvider
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
    Agent 服务
    每个请求创建新实例
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
        # 确保 RAG 服务注入工具中
        inject_rag_service(rag_service)

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
        Agent 聊天的完整编排。

        流程：
          1. 保存用户消息
          2. 加载最近 20 条历史消息
          3. 构建上下文
          4. 启动 ReAct Agent
          5. 循环：保存 Agent 步骤 + 推送 SSE 事件
          6. 发送完成事件
        """
        # ==== Step 1: 保存用户消息 ====
        await self.repo.add_message(
            session_id=session_id,
            role="user",
            content=user_message,
            step_number=0,
        )

        # ==== Step 2: 加载历史消息（最近 20 条）====
        history_msgs = await self.repo.get_last_n_messages(session_id, 20)

        # ==== Step 3: 构建上下文（LangChain 消息格式）====
        from langchain_core.messages import AIMessage, HumanMessage

        context_messages = []
        for msg in history_msgs:
            if msg.role == "user":
                context_messages.append(HumanMessage(content=msg.content))
            elif msg.role == "assistant":
                context_messages.append(AIMessage(content=msg.content))
        # thought / tool_call / observation 不放入上下文
        # 这些是中间推理步骤，放入历史会让上下文过长

        # ==== Step 4: 创建 Agent 实例 ====
        # Agent 用 normal 模型（高质量推理 + tool calling）
        llm_invoker = self.llm_provider.create_invoker("normal")
        tools = get_all_tools()
        agent = ReActAgent(
            llm_invoker=llm_invoker,
            tools=tools,
            max_iterations=8,
        )

        # ==== Step 5: 执行 Agent 循环，边执行边存边推送 ====
        step_number = 0
        final_answer = ""
        try:
            async for event in agent.run(
                user_message=user_message,
                user_id=user_id,
                context_messages=context_messages,
            ):
                event_type = event.get("type", "")

                if event_type == "tool_call":
                    step_number += 1
                    # 保存 tool_call
                    await self.repo.add_message(
                        session_id=session_id,
                        role="tool_call",
                        content=f"调用工具: {event['tool_name']}",
                        metadata_json={
                            "tool_name": event["tool_name"],
                            "arguments": event["arguments"],
                        },
                        step_number=step_number,
                    )

                elif event_type == "observation":
                    step_number += 1
                    # 保存 observation
                    await self.repo.add_message(
                        session_id=session_id,
                        role="observation",
                        content=event["content"],
                        metadata_json={
                            "tool_name": event.get("tool_name", ""),
                        },
                        step_number=step_number,
                    )

                elif event_type == "token":
                    final_answer += event.get("content", "")

                elif event_type == "done":
                    final_answer = event.get("content", "") or final_answer

                # 推送事件给前端
                yield event

        except Exception as e:
            logger.error("Agent stream failed: %s", e)
            yield {"type": "error", "content": f"Agent 执行失败: {e}"}
            return

        # ==== Step 6: 保存最终回答 + 更新标题 ====
        if final_answer:
            await self.repo.add_message(
                session_id=session_id,
                role="assistant",
                content=final_answer,
                step_number=step_number + 1,
            )

            # 自动生成标题（用用户消息的前 20 字）
            if step_number <= 2:  # 第一个回合才更新标题
                title = user_message[:20] + ("..." if len(user_message) > 20 else "")
                await self.repo.update_session_title(session_id, title)

        yield {"type": "done", "content": final_answer}
