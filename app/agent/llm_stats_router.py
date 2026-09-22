"""
LLM 用量统计 API 路由

查询 llm_usage_logs 表，提供：
  GET /llm/usage/summary   用量汇总（总调用/总token/平均延迟/按模型分组）
  GET /llm/usage/logs      用量日志明细（分页）
"""
from datetime import date
from typing import List, Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user, get_db
from app.database.models import LlmUsageLog, User
from app.database.usage_repository import UsageRepository

router = APIRouter(prefix="/llm/usage", tags=["LLM 用量统计"])


def get_usage_repository(db: AsyncSession = Depends(get_db)) -> UsageRepository:
    return UsageRepository(db)


@router.get("/summary")
async def get_usage_summary(
    start_date: Optional[date] = Query(None, description="开始日期 YYYY-MM-DD"),
    end_date: Optional[date] = Query(None, description="结束日期 YYYY-MM-DD"),
    current_user: User = Depends(get_current_user),
    repo: UsageRepository = Depends(get_usage_repository),
):
    """LLM 用量汇总：总调用次数、总 token、平均延迟、按模型分组。"""
    return await repo.get_summary(
        user_id=str(current_user.id),
        start_date=start_date,
        end_date=end_date,
    )


@router.get("/logs")
async def get_usage_logs(
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    current_user: User = Depends(get_current_user),
    repo: UsageRepository = Depends(get_usage_repository),
):
    """当前用户的 LLM 调用日志（按时间倒序）。"""
    logs = await repo.list_logs(
        user_id=str(current_user.id), limit=limit, offset=offset
    )

    def _to_dict(lg: LlmUsageLog) -> dict:
        return {
            "id": str(lg.id),
            "module_name": lg.module_name,
            "model_name": lg.model_name,
            "tool_name": lg.tool_name,
            "input_tokens": lg.input_tokens,
            "output_tokens": lg.output_tokens,
            "total_tokens": lg.total_tokens,
            "duration_ms": lg.duration_ms,
            "created_at": lg.created_at.isoformat(),
        }

    return {"logs": [_to_dict(l) for l in logs], "count": len(logs)}