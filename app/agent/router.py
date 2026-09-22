"""
Agent API 路由

 端点列表：
   POST   /agent/chat                  发送消息（SSE 流式，自动创建/继续会话）
   GET    /agent/sessions              会话列表
   GET    /agent/sessions/{id}         会话详情
   DELETE /agent/sessions/{id}         删除会话
"""
import json
from dataclasses import asdict, is_dataclass
from typing import List


class _JSONEncoder(json.JSONEncoder):
    """支持 dataclass / Source 等对象的 JSON 编码器。"""
    def default(self, o):
        if is_dataclass(o) and not isinstance(o, type):
            return asdict(o)
        return super().default(o)


from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.schemas import (
    AgentChatRequest,
    AgentSessionDetailResponse,
    AgentSessionResponse,
)
from app.agent.service import AgentService
from app.auth.dependencies import get_current_user, get_db
from app.database.models import User

router = APIRouter(prefix="/agent", tags=["Agent"])

# 全局服务引用（由 main.py 注入）
_llm_provider = None
_rag_service = None
_redis_client = None


def init_agent_module(llm_provider, rag_service, redis_client=None):
    """
    初始化 Agent 模块：注入全局服务 + 注册所有 Agent 到 Registry。

    P0 做法：只保存全局引用，每次请求在 AgentService 中硬编码创建 ReActAgent。
    P1 做法：启动时创建所有 Agent 实例 → 注册到 Registry → 请求时按意图路由。

    为什么启动时创建而不是每次请求创建？
      Agent 实例是无状态的（状态在 AgentContext 中传入），启动时创建一次即可复用。
    """
    global _llm_provider, _rag_service, _redis_client
    _llm_provider = llm_provider
    _rag_service = rag_service
    _redis_client = redis_client

    import logging
    logger = logging.getLogger(__name__)

    from app.agent.registry.hub import get_agent_registry
    from app.agent.agents.default import GeneralAgent
    from app.agent.subagents.builtin.recipe_master import RecipeMasterAgent
    from app.agent.subagents.builtin.diet_planner import DietPlannerAgent
    from app.agent.tools import inject_meal_plan_deps, inject_diet_tools_deps
    from app.config import settings

    registry = get_agent_registry()

    # ==== 注入饮食规划工具依赖 ====
    inject_meal_plan_deps(rag_service, llm_provider)

    # ==== 注入饮食管理工具依赖（AI 文字解析用 LLM）====
    inject_diet_tools_deps(llm_provider)

    # ==== 初始化 Reranker ====
    # Reranker 是可选的——配置关了或初始化失败都不影响主流程
    reranker = None
    if settings.reranker_enabled:
        try:
            from app.rag.rerankers.dashscope_reranker import DashScopeReranker
            reranker = DashScopeReranker(
                model=settings.reranker_model,
                top_n=settings.reranker_top_n,
            )
            logger.info("Reranker initialized: model=%s", settings.reranker_model)
        except Exception as e:
            logger.warning("Failed to init Reranker, continuing without: %s", e)

    # 【P1 修复】将 Reranker 注入 RAGService，使其在每次检索时自动精排
    # 之前 Reranker 只传给了 RecipeMasterAgent 但从未被调用，是死代码。
    # 注入到 RAGService 后，所有通过 rag_service.retrieve() 的查询
    # （包括 search_recipes 工具、GeneralAgent 的 RAG 检索）都能享受精排。
    if reranker:
        rag_service.set_reranker(reranker)

    # ==== 创建 Agent 实例 ====
    general_agent = GeneralAgent(llm_provider, rag_service)
    recipe_master = RecipeMasterAgent(llm_provider, rag_service, reranker=reranker)
    diet_planner = DietPlannerAgent(llm_provider, rag_service)

    # ==== 注册到 Registry ====
    # 只注册 config.yml 中 enabled_agents 列表里启用的
    enabled = settings.enabled_agents
    if "general" in enabled:
        registry.register(general_agent)
        registry.set_default("general")  # 未匹配意图时的兜底 Agent
    if "recipe_master" in enabled:
        registry.register(recipe_master)
    if "diet_planner" in enabled:
        registry.register(diet_planner)

    logger.info("Agent registry initialized with: %s", registry.list())
    logger.info("Intent routing table: %s", registry.list_routes())


def get_agent_service(db: AsyncSession = Depends(get_db)) -> AgentService:
    """
    FastAPI 依赖注入：每请求创建一个 AgentService 实例。

    传入 _redis_client 以启用意图缓存——相同查询的意图识别结果
    会被缓存到 Redis（TTL 由 settings.intent_cache_ttl 控制），
    避免每次请求都调 fast LLM（~300ms）。
    """
    return AgentService(db, _llm_provider, _rag_service, _redis_client)


# ===== 会话管理 =====

@router.get("/sessions", response_model=List[AgentSessionResponse])
async def list_sessions(
    current_user: User = Depends(get_current_user),
    service: AgentService = Depends(get_agent_service),
):
    """列出当前用户的所有 Agent 会话。"""
    return await service.list_sessions(current_user.id)


@router.get("/sessions/{session_id}", response_model=AgentSessionDetailResponse)
async def get_session(
    session_id: str,
    current_user: User = Depends(get_current_user),
    service: AgentService = Depends(get_agent_service),
):
    """获取 Agent 会话详情（含消息列表）。"""
    sess = await service.get_session_detail(session_id, current_user.id)
    if sess is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    return sess


@router.delete("/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_session(
    session_id: str,
    current_user: User = Depends(get_current_user),
    service: AgentService = Depends(get_agent_service),
):
    """删除 Agent 会话。"""
    deleted = await service.delete_session(session_id, current_user.id)
    if not deleted:
        raise HTTPException(status_code=404, detail="会话不存在")
    return None


# ===== 饮食计划文件下载 =====

@router.get("/meal-plan/download/{filename}")
async def download_meal_plan(filename: str):
    """下载导出的饮食计划文件（Markdown / ICS / HTML）。

    安全：文件名必须匹配白名单格式（meal_plan_YYYYMMDD_<hex8>.<ext>），
    且解析后的真实路径必须位于导出目录内，杜绝路径穿越。
    导出文件名含随机 uuid，URL 不可猜测，因此该端点无需登录态
    （前端 <a download> 直接点击即可）。
    """
    import os

    from app.agent.tools.meal_plan_tools import (
        EXPORT_MEDIA_TYPES,
        get_plan_storage_dir,
        is_safe_plan_filename,
    )

    if not is_safe_plan_filename(filename):
        raise HTTPException(status_code=404, detail="文件不存在")

    storage_dir = get_plan_storage_dir()
    filepath = os.path.abspath(os.path.join(storage_dir, filename))
    if os.path.commonpath([filepath, storage_dir]) != storage_dir:
        raise HTTPException(status_code=404, detail="文件不存在")
    if not os.path.isfile(filepath):
        raise HTTPException(status_code=404, detail="文件不存在")

    ext = os.path.splitext(filename)[1].lower()
    return FileResponse(
        filepath,
        media_type=EXPORT_MEDIA_TYPES.get(ext, "application/octet-stream"),
        filename=filename,
    )


# ===== 核心：Agent 聊天（SSE 流式）=====

@router.post("/chat")
async def agent_chat(
    body: AgentChatRequest,
    current_user: User = Depends(get_current_user),
    service: AgentService = Depends(get_agent_service),
):
    """
    Agent 对话接口（SSE 流式响应）。

    如果传了 session_id，继续已有会话；否则自动创建新会话。

    SSE 事件类型：
      - tool_call:   Agent 正在调用工具
      - observation: 工具返回的结果
      - thought:     Agent 的思考过程
      - token:       最终回答的逐 token 输出
      - done:        对话完成
      - error:       出错
    """
    # 处理会话（自动创建或获取已有）
    if body.session_id:
        sess = await service.repo.get_session(body.session_id)
        if sess is None or sess.user_id != current_user.id:
            raise HTTPException(status_code=404, detail="会话不存在")
        session_id = body.session_id
    else:
        sess = await service.create_session(current_user.id)
        session_id = sess.id

    async def event_stream():
        """SSE 事件生成器。"""
        # 先发送 session_id，让前端知道是哪个会话
        yield f"event: session\ndata: {json.dumps({'session_id': session_id}, ensure_ascii=False, cls=_JSONEncoder)}\n\n"

        async for event in service.stream_agent_chat(
            user_id=current_user.id,
            session_id=session_id,
            user_message=body.content,
        ):
            event_type = event.get("type", "message")
            yield f"event: {event_type}\ndata: {json.dumps(event, ensure_ascii=False, cls=_JSONEncoder)}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
