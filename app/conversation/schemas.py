"""
对话 API 的 Pydantic 模型
"""
from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field


# ---- 对话 ----

class ConversationCreateRequest(BaseModel):
    """创建对话请求。"""
    title: str = Field(default="新对话", max_length=255)


class ConversationResponse(BaseModel):
    """对话摘要（列表用）。"""
    id: str
    title: str
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class ConversationDetailResponse(BaseModel):
    """对话详情（含消息列表）。"""
    id: str
    title: str
    created_at: datetime
    updated_at: datetime
    messages: List["MessageResponse"] = []

    class Config:
        from_attributes = True


# ---- 消息 ----

class ChatRequest(BaseModel):
    """发送消息请求。"""
    content: str = Field(..., min_length=1, max_length=5000)


class MessageResponse(BaseModel):
    """单条消息。"""
    id: str
    role: str
    content: str
    created_at: datetime
    token_count: Optional[int] = None

    class Config:
        from_attributes = True


# ---- 引用来源 ----

class SourceInfo(BaseModel):
    """检索到的菜谱来源信息（在 SSE 事件中发送给前端）。"""
    dish_name: str
    category: str
    source: str
    relevance_score: float


#   关键点：
#   - ChatRequest.content 限制 5000 字符——防止恶意请求消耗大量 token
#   - from_attributes = True 让 FastAPI 可以直接把 SQLAlchemy ORM 对象序列化为 JSON
#   - SourceInfo 单独定义——在流式响应的最后，前端可以展示"参考了哪些菜谱"
