"""
饮食管理 API 路由

端点列表：
  计划管理：
    GET    /diet/plans              获取某周饮食计划 (?week_start=YYYY-MM-DD)
    POST   /diet/plans              添加/更新一顿计划餐
    PATCH  /diet/plans/{meal_id}    修改某餐
    DELETE /diet/plans/{meal_id}    删除某餐
    POST   /diet/plans/{meal_id}/copy    复制某餐到另一天
    POST   /diet/plans/{meal_id}/mark-eaten  标记已吃 → 自动生成记录
  记录管理：
    GET    /diet/logs               查询某天记录 (?log_date=YYYY-MM-DD)
    POST   /diet/logs               手动记录
    POST   /diet/logs/from-text     AI 解析自然语言记录
    DELETE /diet/logs/{log_id}      删除记录
  营养分析：
    GET    /diet/analysis/daily     日汇总
    GET    /diet/analysis/weekly    周汇总
    GET    /diet/analysis/deviation 计划 vs 实际偏差
    GET    /diet/preferences        查看偏好
    PUT    /diet/preferences        更新偏好
"""
import logging
from datetime import date
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user, get_db
from app.database.models import User
from app.diet.schemas import (
    AddMealRequest,
    CopyMealRequest,
    CreateLogRequest,
    LogFromTextRequest,
    UpdateMealRequest,
    UpdatePreferenceRequest,
)
from app.diet.service import DietService
from app.diet.models import MealType

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/diet", tags=["饮食管理"])

# 全局 LLM Provider（由 main.py 注入，用于 AI 解析）
_llm_provider = None


def init_diet_module(llm_provider=None):
    """在应用启动时调用，注入 LLM Provider。"""
    global _llm_provider
    _llm_provider = llm_provider


def get_diet_service(db: AsyncSession = Depends(get_db)) -> DietService:
    return DietService(db)


def _validate_meal_type(meal_type: str):
    if meal_type not in MealType.ALL:
        raise HTTPException(status_code=422, detail=f"无效餐次类型: {meal_type}")


# ==================== 计划管理 ====================

@router.get("/plans")
async def get_plan_by_week(
    week_start: date,
    current_user: User = Depends(get_current_user),
    service: DietService = Depends(get_diet_service),
):
    """获取某周的饮食计划。week_start 为该周周一日期。"""
    return await service.get_plan_by_week(str(current_user.id), week_start)


@router.post("/plans", status_code=201)
async def add_meal(
    body: AddMealRequest,
    current_user: User = Depends(get_current_user),
    service: DietService = Depends(get_diet_service),
):
    """添加/更新一顿计划餐（同日期同餐次则覆盖）。"""
    _validate_meal_type(body.meal_type)
    dishes = [d.model_dump() for d in body.dishes] if body.dishes else None
    return await service.add_meal(
        str(current_user.id), body.plan_date, body.meal_type,
        dishes=dishes, notes=body.notes,
    )


@router.patch("/plans/{meal_id}")
async def update_meal(
    meal_id: str,
    body: UpdateMealRequest,
    current_user: User = Depends(get_current_user),
    service: DietService = Depends(get_diet_service),
):
    meal = await service.update_meal(
        meal_id, str(current_user.id),
        dishes=[d.model_dump() for d in body.dishes] if body.dishes is not None else None,
        notes=body.notes,
    )
    if meal is None:
        raise HTTPException(status_code=404, detail="餐次不存在或无权访问")
    return meal


@router.delete("/plans/{meal_id}")
async def delete_meal(
    meal_id: str,
    current_user: User = Depends(get_current_user),
    service: DietService = Depends(get_diet_service),
):
    ok = await service.delete_meal(meal_id, str(current_user.id))
    if not ok:
        raise HTTPException(status_code=404, detail="餐次不存在或无权访问")
    return {"message": "餐次已删除"}


@router.post("/plans/{meal_id}/copy")
async def copy_meal(
    meal_id: str,
    body: CopyMealRequest,
    current_user: User = Depends(get_current_user),
    service: DietService = Depends(get_diet_service),
):
    meal = await service.copy_meal(
        meal_id, str(current_user.id), body.target_date, body.target_meal_type
    )
    if meal is None:
        raise HTTPException(status_code=404, detail="餐次不存在或无权访问")
    return meal


@router.post("/plans/{meal_id}/mark-eaten")
async def mark_plan_meal_eaten(
    meal_id: str,
    log_date: Optional[date] = None,
    current_user: User = Depends(get_current_user),
    service: DietService = Depends(get_diet_service),
):
    """把计划中的一餐标记为已吃，自动生成饮食记录。"""
    log = await service.mark_plan_meal_as_eaten(
        str(current_user.id), meal_id, log_date
    )
    if log is None:
        raise HTTPException(status_code=404, detail="餐次不存在或无权访问")
    return log


# ==================== 记录管理 ====================

@router.get("/logs")
async def get_logs(
    log_date: date,
    current_user: User = Depends(get_current_user),
    service: DietService = Depends(get_diet_service),
):
    """查询某天的饮食记录。"""
    logs = await service.get_logs_by_date(str(current_user.id), log_date)
    return {"logs": logs, "date": log_date.isoformat()}


@router.post("/logs", status_code=201)
async def create_log(
    body: CreateLogRequest,
    current_user: User = Depends(get_current_user),
    service: DietService = Depends(get_diet_service),
):
    """手动记录饮食。"""
    _validate_meal_type(body.meal_type)
    items = [it.model_dump() for it in body.items] if body.items else None
    return await service.log_manual(
        str(current_user.id), body.log_date, body.meal_type,
        items=items, notes=body.notes, plan_meal_id=body.plan_meal_id,
    )


@router.post("/logs/from-text", status_code=201)
async def create_log_from_text(
    body: LogFromTextRequest,
    current_user: User = Depends(get_current_user),
    service: DietService = Depends(get_diet_service),
):
    """AI 解析自然语言饮食描述并自动记录（自动估算热量）。"""
    return await service.log_from_text(
        str(current_user.id),
        body.text,
        log_date=body.log_date,
        meal_type=body.meal_type,
        llm_provider=_llm_provider,
    )


@router.delete("/logs/{log_id}")
async def delete_log(
    log_id: str,
    current_user: User = Depends(get_current_user),
    service: DietService = Depends(get_diet_service),
):
    ok = await service.delete_log(str(current_user.id), log_id)
    if not ok:
        raise HTTPException(status_code=404, detail="记录不存在或无权访问")
    return {"message": "记录已删除"}


# ==================== 营养分析 ====================

@router.get("/analysis/daily")
async def get_daily_summary(
    target_date: date,
    current_user: User = Depends(get_current_user),
    service: DietService = Depends(get_diet_service),
):
    return await service.get_daily_summary(str(current_user.id), target_date)


@router.get("/analysis/weekly")
async def get_weekly_summary(
    week_start: Optional[date] = None,
    current_user: User = Depends(get_current_user),
    service: DietService = Depends(get_diet_service),
):
    return await service.get_weekly_summary(str(current_user.id), week_start)


@router.get("/analysis/deviation")
async def get_deviation_analysis(
    week_start: Optional[date] = None,
    current_user: User = Depends(get_current_user),
    service: DietService = Depends(get_diet_service),
):
    return await service.get_deviation_analysis(str(current_user.id), week_start)


# ==================== 用户偏好 ====================

@router.get("/preferences")
async def get_preferences(
    current_user: User = Depends(get_current_user),
    service: DietService = Depends(get_diet_service),
):
    pref = await service.get_user_preference(str(current_user.id))
    if pref is None:
        return {"message": "暂无偏好设置，可用 GET /diet/preferences 查看", "preference": None}
    return {"preference": pref}


@router.put("/preferences")
async def update_preferences(
    body: UpdatePreferenceRequest,
    current_user: User = Depends(get_current_user),
    service: DietService = Depends(get_diet_service),
):
    update_data = body.model_dump(exclude_unset=True)
    pref = await service.update_user_preference(str(current_user.id), **update_data)
    return {"preference": pref}