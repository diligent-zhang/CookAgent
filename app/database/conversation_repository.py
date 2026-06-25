"""
对话和消息的数据访问层（Repository Pattern）。
封装对 conversations 和 messages 表的 CRUD 操作。
"""
import uuid
from datetime import datetime
from typing import Dict, List, Optional

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import Conversation, Message


class ConversationRepository:
    """
    对话仓库。
    所有方法都接收 AsyncSession 作为参数（由调用方管理事务边界）。
    """

    def __init__(self, session: AsyncSession):
        self.session = session

    # ========== Conversation 操作 ==========

    async def create_conversation(
        self, user_id: str, title: str = "新对话"
    ) -> Conversation:
        """创建新对话。"""
        conv = Conversation(
            id=str(uuid.uuid4()),
            user_id=user_id,
            title=title,
        )
        self.session.add(conv)
        await self.session.commit()
        await self.session.refresh(conv)
        return conv

    async def get_conversation(self, conv_id: str) -> Optional[Conversation]:
        """根据 ID 获取对话。"""
        result = await self.session.execute(
            select(Conversation).where(Conversation.id == conv_id)
        )
        return result.scalar_one_or_none()

    async def list_conversations(self, user_id: str) -> List[Conversation]:
        """列出用户的所有对话（按更新时间倒序）。"""
        result = await self.session.execute(
            select(Conversation)
            .where(Conversation.user_id == user_id)
            .order_by(Conversation.updated_at.desc())
        )
        return list(result.scalars().all())

    async def update_conversation(self, conv_id: str, **kwargs) -> None:
        """更新对话字段（如标题、压缩摘要等）。"""
        kwargs["updated_at"] = datetime.utcnow()
        await self.session.execute(
            update(Conversation)
            .where(Conversation.id == conv_id)
            .values(**kwargs)
        )
        await self.session.commit()

    async def delete_conversation(self, conv_id: str) -> bool:
        """删除对话（级联删除消息）。"""
        conv = await self.get_conversation(conv_id)
        if not conv:
            return False
        await self.session.delete(conv)
        await self.session.commit()
        return True

    # ========== Message 操作 ==========

    async def add_message(
        self,
        conversation_id: str,
        role: str,
        content: str,
        token_count: Optional[int] = None,
    ) -> Message:
        """向对话中添加一条消息。"""
        msg = Message(
            id=str(uuid.uuid4()),
            conversation_id=conversation_id,
            role=role,
            content=content,
            token_count=token_count,
        )
        self.session.add(msg)
        # 同时更新对话的 updated_at
        await self.session.execute(
            update(Conversation)
            .where(Conversation.id == conversation_id)
            .values(updated_at=datetime.utcnow())
        )
        await self.session.commit()
        await self.session.refresh(msg)
        return msg

    async def get_messages(self, conversation_id: str) -> List[Message]:
        """获取对话的所有消息（按时间正序）。"""
        result = await self.session.execute(
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .order_by(Message.created_at.asc())
        )
        return list(result.scalars().all())

    async def get_history_as_dicts(
        self, conversation_id: str
    ) -> List[Dict[str, str]]:
        """
        获取对话历史，返回为 LLM 可用的 dict 列表。
        格式：[{"role": "user", "content": "..."}, {"role": "assistant", "content": "..."}]
        """
        messages = await self.get_messages(conversation_id)
        return [{"role": m.role, "content": m.content} for m in messages]


#   模块解释
#
#   Repository Pattern 的核心思想：业务代码（Service）不直接写 SQL，
#   而是通过 Repository 的方法来表达意图。
#
#   Service 层:
#     repo = ConversationRepository(session)
#     conv = await repo.create_conversation(user_id, "今天吃什么")
#
#     而不是:
#     conv = Conversation(id=..., user_id=..., title=...)
#     session.add(conv)
#     await session.commit()
#
#   为什么 AsyncSession 由调用方传入？
#   - 同一个请求里的多个数据库操作应该在同一事务边界内
#   - 如果 Repository 内部自己管理 session，就无法和其他 Repository 共享事务
#   - 传入 session 让调用方（通常是 Service 层或 FastAPI 的 dependency）控制事务范围
#
#   session.refresh(conv)：提交后从数据库重新加载对象，
#     确保拿到数据库生成的默认值和修改后的状态。
