"""
Agent 会话仓库
"""
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.models import AgentMessage, AgentSession


class AgentSessionRepository:
    """Agent 会话的 CRUD 仓库。"""

    def __init__(self, session: AsyncSession):
        self.session = session

    # ===== Session 操作 =====

    async def create_session(self, user_id: str, title: str = "新对话") -> AgentSession:
        sess = AgentSession(id=str(uuid.uuid4()), user_id=user_id, title=title)
        self.session.add(sess)
        await self.session.commit()
        await self.session.refresh(sess)
        return sess

    async def get_session(self, session_id: str) -> Optional[AgentSession]:
        result = await self.session.execute(
            select(AgentSession).where(AgentSession.id == session_id)
        )
        return result.scalar_one_or_none()

    async def list_sessions(self, user_id: str) -> List[AgentSession]:
        result = await self.session.execute(
            select(AgentSession)
            .where(AgentSession.user_id == user_id)
            .order_by(AgentSession.updated_at.desc())
        )
        return list(result.scalars().all())

    async def update_session_title(self, session_id: str, title: str) -> None:
        await self.session.execute(
            update(AgentSession)
            .where(AgentSession.id == session_id)
            .values(title=title, updated_at=datetime.utcnow())
        )
        await self.session.commit()

    async def delete_session(self, session_id: str) -> bool:
        sess = await self.get_session(session_id)
        if not sess:
            return False
        await self.session.delete(sess)
        await self.session.commit()
        return True

    # ===== Message 操作 =====

    async def add_message(
        self,
        session_id: str,
        role: str,
        content: Optional[str] = None,
        metadata_json: Optional[Dict[str, Any]] = None,
        step_number: int = 0,
    ) -> AgentMessage:
        msg = AgentMessage(
            id=str(uuid.uuid4()),
            session_id=session_id,
            role=role,
            content=content,
            metadata_json=metadata_json,
            step_number=step_number,
        )
        self.session.add(msg)

        # 同步更新 session 的 updated_at
        await self.session.execute(
            update(AgentSession)
            .where(AgentSession.id == session_id)
            .values(updated_at=datetime.utcnow())
        )
        await self.session.commit()
        await self.session.refresh(msg)
        return msg

    async def get_messages(self, session_id: str) -> List[AgentMessage]:
        result = await self.session.execute(
            select(AgentMessage)
            .where(AgentMessage.session_id == session_id)
            .order_by(AgentMessage.created_at.asc())
        )
        return list(result.scalars().all())

    async def get_last_n_messages(self, session_id: str, n: int) -> List[AgentMessage]:
        """获取最后 N 条消息（用于构建 Agent 上下文窗口）。"""
        result = await self.session.execute(
            select(AgentMessage)
            .where(AgentMessage.session_id == session_id)
            .order_by(AgentMessage.created_at.desc())
            .limit(n)
        )
        return list(reversed(list(result.scalars().all())))


#相当于java中的dao层，用于数据访问，service去做操作