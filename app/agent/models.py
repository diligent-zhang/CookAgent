"""
Agent 会话模型

Agent 会话 vs RAG Conversation：
  - RAG Conversation 是"用户问 → 检索 → 回答"的简单对话
  - Agent Session    是"用户问 → 思考 → 调用工具 → 观察结果 → 再思考 → 回答"
  每一步都可能涉及多次 LLM 调用和工具调用

所以 Agent 需要独立的 session 表和 message 表来记录这个复杂的过程。
"""
import uuid as uuid_module
from datetime import datetime

from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    JSON,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.database.session import Base


def generate_uuid():
    return str(uuid_module.uuid4())


class AgentSession(Base):
    """
    Agent 会话表。

    一次 Agent 对话创建一个 session。
    每个 session 包含多轮 ReAct 循环（用户发一条消息 → Agent 多步推理 → 最终回答）。
    """
    __tablename__ = "agent_sessions"

    id = Column(UUID(as_uuid=False), primary_key=True, default=generate_uuid)
    user_id = Column(
        UUID(as_uuid=False), ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    title = Column(String(255), default="新对话")
    status = Column(
        String(16), default="active",
        comment="会话状态：active（进行中）、completed（已完成）、error（出错）"
    )
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # 关系
    owner = relationship("User", back_populates="agent_sessions")
    messages = relationship(
        "AgentMessage", back_populates="session", lazy="dynamic"
    )


class AgentMessage(Base):
    """
    Agent 消息表。

    记录 Agent 会话中的每一步，包括：
      - user:        用户输入
      - thought:      Agent 的思考过程（"我需要先查一下..."）
      - tool_call:    Agent 决定调用哪个工具、传什么参数
      - observation:  工具返回的结果
      - assistant:    Agent 最终给用户的回答
    """
    __tablename__ = "agent_messages"

    id = Column(UUID(as_uuid=False), primary_key=True, default=generate_uuid)
    session_id = Column(
        UUID(as_uuid=False), ForeignKey("agent_sessions.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    # 消息类型：user / thought / tool_call / observation / assistant
    role = Column(String(16), nullable=False)
    # 文本内容
    content = Column(Text, nullable=True)
    # 额外数据（JSON）:
    #   tool_call 时存 {"tool_name": "search_recipes", "arguments": {...}}
    #   observation 时存 {"tool_name": "search_recipes", "result": {...}}
    metadata_json = Column(JSON, nullable=True)
    # 在同一次 ReAct 循环中的步骤序号（用于排序）
    step_number = Column(Integer, default=0)
    # 创建时间
    created_at = Column(DateTime, default=datetime.utcnow)

    # 关系
    session = relationship("AgentSession", back_populates="messages")


# 需要在 User 模型中新增反向关系（在 app/database/models.py 的 User 类里加一行）
# agent_sessions = relationship(
#     "AgentSession", back_populates="owner", lazy="dynamic"
# )
