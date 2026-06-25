"""
Agent API 的 Pydantic 模型
"""
from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class AgentChatRequest(BaseModel):
    """Agent 聊天请求。"""
    content: str = Field(..., min_length=1, max_length=5000)
    session_id: Optional[str] = Field(
        default=None,
        description="已有会话ID，不传则创建新会话"
    )


class AgentSessionResponse(BaseModel):
    """Agent 会话摘要"""
    id: str
    title: str
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class AgentSessionDetailResponse(BaseModel):
    """Agent 会话详情（含消息列表）。"""
    id: str
    title: str
    status: str
    created_at: datetime
    updated_at: datetime
    messages: List["AgentMessageResponse"] = []

    class Config:
        from_attributes = True


class AgentMessageResponse(BaseModel):
    """Agent 消息。"""
    id: str
    role: str
    content: Optional[str] = None
    metadata_json: Optional[Dict[str, Any]] = None
    step_number: int
    created_at: datetime

    class Config:
        from_attributes = True
