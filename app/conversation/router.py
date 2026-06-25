"""
对话 API 路由

 端点列表：
   POST   /conversations                 创建对话
   GET    /conversations                 对话列表
   GET    /conversations/{id}            对话详情
   DELETE /conversations/{id}            删除对话
   POST   /conversations/{id}/chat       发送消息（SSE 流式响应）
   GET    /conversations/{id}/messages   消息列表
"""

import json
from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user, get_db
from app.conversation.schemas import (
    ChatRequest,
    ConversationCreateRequest,
    ConversationDetailResponse,
    ConversationResponse,
    MessageResponse,
)
from app.conversation.service import ConversationService
from app.database.models import User
from app.llm.provider import LLMProvider
from app.rag.service import RAGService

router = APIRouter(prefix="/conversations", tags=["对话"])


# ===== 全局服务实例（在 lifespan 中初始化） =====
# 这些由 main.py 在启动时注入
_llm_provider: LLMProvider = None
_rag_service: RAGService = None


def init_conversation_module(llm_provider: LLMProvider, rag_service: RAGService):
    """在应用启动时调用，注入全局服务实例。"""
    global _llm_provider, _rag_service
    _llm_provider = llm_provider
    _rag_service = rag_service


def get_conversation_service(db: AsyncSession = Depends(get_db)) -> ConversationService:
    """FastAPI 依赖：创建对话服务实例。"""
    return ConversationService(db, _llm_provider, _rag_service)


# ===== 对话 CRUD =====

@router.post("", response_model=ConversationResponse, status_code=status.HTTP_201_CREATED)
async def create_conversation(
    body: ConversationCreateRequest,
    current_user: User = Depends(get_current_user),
    service: ConversationService = Depends(get_conversation_service),
):
    """创建新对话。"""
    return await service.create_conversation(current_user.id, body.title)


@router.get("", response_model=List[ConversationResponse])
async def list_conversations(
    current_user: User = Depends(get_current_user),
    service: ConversationService = Depends(get_conversation_service),
):
    """列出当前用户的所有对话（按更新时间倒序）。"""
    return await service.list_conversations(current_user.id)


@router.get("/{conv_id}", response_model=ConversationDetailResponse)
async def get_conversation(
    conv_id: str,
    current_user: User = Depends(get_current_user),
    service: ConversationService = Depends(get_conversation_service),
):
    """获取对话详情（含消息列表）。"""
    conv = await service.get_conversation_detail(conv_id, current_user.id)
    if conv is None:
        raise HTTPException(status_code=404, detail="对话不存在")
    return conv


@router.delete("/{conv_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_conversation(
    conv_id: str,
    current_user: User = Depends(get_current_user),
    service: ConversationService = Depends(get_conversation_service),
):
    """删除对话（级联删除所有消息）。"""
    deleted = await service.delete_conversation(conv_id, current_user.id)
    if not deleted:
        raise HTTPException(status_code=404, detail="对话不存在")
    return None


# ===== 流式聊天（核心接口） =====

@router.post("/{conv_id}/chat")
async def chat(
    conv_id: str,
    body: ChatRequest,
    current_user: User = Depends(get_current_user),
    service: ConversationService = Depends(get_conversation_service),
):
    """
    向对话发送消息，返回 SSE 流式响应。

    事件类型：
      - thinking: 检索中
      - sources:  检索到的菜谱引用
      - token:    逐 token 输出
      - done:     回答完成
      - error:    错误信息
    """
    # 先校验对话存在且属于当前用户
    conv = await service.repo.get_conversation(conv_id)
    if conv is None or conv.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="对话不存在")

    async def event_stream():
        """SSE 事件生成器。"""
        async for event in service.stream_chat(
            user_id=current_user.id,
            conv_id=conv_id,
            user_message=body.content,
        ):
            # SSE 格式：event: <type>\ndata: <json>\n\n
            event_type = event.get("type", "message")
            yield f"event: {event_type}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # 禁用 Nginx 缓冲
        },
    )


# ===== 消息历史 =====

@router.get("/{conv_id}/messages", response_model=List[MessageResponse])
async def get_messages(
    conv_id: str,
    current_user: User = Depends(get_current_user),
    service: ConversationService = Depends(get_conversation_service),
):
    """获取对话的所有消息（按时间正序）。"""
    conv = await service.repo.get_conversation(conv_id)
    if conv is None or conv.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="对话不存在")
    return await service.repo.get_messages(conv_id)


# ===== SSE 事件流示例（前端收到的原始数据） =====
#
# event: thinking
# data: {"content": "正在检索相关菜谱..."}
#
# event: sources
# data: {"sources": [{"dish_name": "番茄炒蛋", "category": "家常菜", "relevance_score": 0.93}]}
#
# event: token
# data: {"content": "番"}
#
# event: token
# data: {"content": "茄"}
#
# event: token
# data: {"content": "炒"}
#
# event: token
# data: {"content": "蛋"}
#
# event: done
# data: {"message_id": "abc-123"}
