"""
饮食模块数据访问层（Repository Pattern）。

Repository 封装对 diet_plan_meals / diet_log_items / user_food_preferences
三张表的 CRUD 和统计分析，Service 层不直接写 SQL。
"""
import uuid
from datetime import date, timedelta
from typing import List, Optional

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.diet.models import DietLogItem, DietPlanMeal, UserFoodPreference


# ============================================================
# 日期工具
# ============================================================

def get_week_start(target: date) -> date:
    """返回 target 所在周的周一。"""
    return target - timedelta(days=target.weekday())


# ============================================================
# Repository
# ============================================================

class DietRepository:
    """饮食模块的数据访问层。"""

    def __init__(self, session: AsyncSession):
        self.session = session

    # ---------- 计划餐次 ----------

    async def get_plan_meal(self, meal_id: str) -> Optional[DietPlanMeal]:
        result = await self.session.execute(
            select(DietPlanMeal).where(DietPlanMeal.id == meal_id)
        )
        return result.scalar_one_or_none()

    async def get_plan_meals_by_week(
        self, user_id: str, week_start: date
    ) -> List[DietPlanMeal]:
        """获取某周（week_start 周一 ~ 周日）内所有计划餐次。"""
        week_end = week_start + timedelta(days=6)
        result = await self.session.execute(
            select(DietPlanMeal)
            .where(
                DietPlanMeal.user_id == user_id,
                DietPlanMeal.plan_date >= week_start,
                DietPlanMeal.plan_date <= week_end,
            )
            .order_by(DietPlanMeal.plan_date, DietPlanMeal.meal_type)
        )
        return list(result.scalars().all())

    async def get_plan_meals_by_date(
        self, user_id: str, target_date: date
    ) -> List[DietPlanMeal]:
        result = await self.session.execute(
            select(DietPlanMeal)
            .where(
                DietPlanMeal.user_id == user_id,
                DietPlanMeal.plan_date == target_date,
            )
            .order_by(DietPlanMeal.meal_type)
        )
        return list(result.scalars().all())

    async def upsert_plan_meal(
        self,
        user_id: str,
        plan_date: date,
        meal_type: str,
        dishes: Optional[list] = None,
        notes: Optional[str] = None,
    ) -> DietPlanMeal:
        """添加/更新计划餐次（同用户同日期同餐次则更新）。"""
        totals = _sum_dishes(dishes)

        result = await self.session.execute(
            select(DietPlanMeal).where(
                DietPlanMeal.user_id == user_id,
                DietPlanMeal.plan_date == plan_date,
                DietPlanMeal.meal_type == meal_type,
            )
        )
        meal = result.scalar_one_or_none()

        if meal is None:
            meal = DietPlanMeal(
                id=str(uuid.uuid4()),
                user_id=user_id,
                plan_date=plan_date,
                meal_type=meal_type,
                dishes=dishes or [],
                notes=notes,
            )
            self.session.add(meal)
        else:
            meal.dishes = dishes or []
            if notes is not None:
                meal.notes = notes

        meal.total_calories = totals["calories"]
        meal.total_protein = totals["protein"]
        meal.total_fat = totals["fat"]
        meal.total_carbs = totals["carbs"]

        await self.session.commit()
        await self.session.refresh(meal)
        return meal

    async def update_plan_meal(self, meal_id: str, **kwargs) -> Optional[DietPlanMeal]:
        meal = await self.get_plan_meal(meal_id)
        if meal is None:
            return None

        if "dishes" in kwargs and kwargs["dishes"] is not None:
            dishes = kwargs["dishes"]
            meal.dishes = dishes
            totals = _sum_dishes(dishes)
            meal.total_calories = totals["calories"]
            meal.total_protein = totals["protein"]
            meal.total_fat = totals["fat"]
            meal.total_carbs = totals["carbs"]
        elif "dishes" in kwargs:
            meal.dishes = []
            meal.total_calories = meal.total_protein = meal.total_fat = meal.total_carbs = None

        if "notes" in kwargs:
            meal.notes = kwargs["notes"]

        await self.session.commit()
        await self.session.refresh(meal)
        return meal

    async def delete_plan_meal(self, meal_id: str) -> bool:
        meal = await self.get_plan_meal(meal_id)
        if meal is None:
            return False
        await self.session.delete(meal)
        await self.session.commit()
        return True

    async def copy_plan_meal(
        self,
        meal_id: str,
        target_date: date,
        target_meal_type: Optional[str] = None,
    ) -> Optional[DietPlanMeal]:
        """复制某餐到另一天/另一餐次。"""
        source = await self.get_plan_meal(meal_id)
        if source is None:
            return None

        new = DietPlanMeal(
            id=str(uuid.uuid4()),
            user_id=source.user_id,
            plan_date=target_date,
            meal_type=target_meal_type or source.meal_type,
            dishes=source.dishes or [],
            total_calories=source.total_calories,
            total_protein=source.total_protein,
            total_fat=source.total_fat,
            total_carbs=source.total_carbs,
            notes=source.notes,
        )
        self.session.add(new)
        await self.session.commit()
        await self.session.refresh(new)
        return new

    # ---------- 饮食记录 ----------

    async def create_log_items(
        self,
        user_id: str,
        log_date: date,
        meal_type: str,
        items: List[dict],
        notes: Optional[str] = None,
        plan_meal_id: Optional[str] = None,
        log_id: Optional[str] = None,
    ) -> List[DietLogItem]:
        """创建一次饮食记录（同 log_id 的多条食物）。"""
        log_id = log_id or str(uuid.uuid4())
        created = []
        for item in items:
            entry = DietLogItem(
                id=str(uuid.uuid4()),
                log_id=log_id,
                user_id=user_id,
                log_date=log_date,
                meal_type=meal_type,
                plan_meal_id=plan_meal_id,
                food_name=item.get("food_name", "未记录食物"),
                weight_g=item.get("weight_g"),
                unit=item.get("unit"),
                calories=item.get("calories"),
                protein=item.get("protein"),
                fat=item.get("fat"),
                carbs=item.get("carbs"),
                source=item.get("source", "manual"),
                confidence_score=item.get("confidence_score"),
                notes=notes,
            )
            self.session.add(entry)
            created.append(entry)

        await self.session.commit()
        for e in created:
            await self.session.refresh(e)
        return created

    async def get_log_items_by_date(
        self, user_id: str, log_date: date
    ) -> List[DietLogItem]:
        result = await self.session.execute(
            select(DietLogItem)
            .where(DietLogItem.user_id == user_id, DietLogItem.log_date == log_date)
            .order_by(DietLogItem.created_at)
        )
        return list(result.scalars().all())

    async def get_log_items_by_range(
        self, user_id: str, start: date, end: date
    ) -> List[DietLogItem]:
        result = await self.session.execute(
            select(DietLogItem)
            .where(
                DietLogItem.user_id == user_id,
                DietLogItem.log_date >= start,
                DietLogItem.log_date <= end,
            )
            .order_by(DietLogItem.log_date, DietLogItem.created_at)
        )
        return list(result.scalars().all())

    async def get_log_items_by_log_id(self, log_id: str) -> List[DietLogItem]:
        result = await self.session.execute(
            select(DietLogItem).where(DietLogItem.log_id == log_id).order_by(DietLogItem.created_at)
        )
        return list(result.scalars().all())

    async def delete_log(self, log_id: str) -> bool:
        result = await self.session.execute(
            delete(DietLogItem).where(DietLogItem.log_id == log_id)
        )
        await self.session.commit()
        return result.rowcount > 0

    async def add_item_to_log(
        self, log_id: str, log_date: date, meal_type: str, user_id: str, **kwargs
    ) -> DietLogItem:
        entry = DietLogItem(
            id=str(uuid.uuid4()),
            log_id=log_id,
            user_id=user_id,
            log_date=log_date,
            meal_type=meal_type,
            food_name=kwargs.get("food_name", "未记录食物"),
            weight_g=kwargs.get("weight_g"),
            unit=kwargs.get("unit"),
            calories=kwargs.get("calories"),
            protein=kwargs.get("protein"),
            fat=kwargs.get("fat"),
            carbs=kwargs.get("carbs"),
            source=kwargs.get("source", "manual"),
            confidence_score=kwargs.get("confidence_score"),
        )
        self.session.add(entry)
        await self.session.commit()
        await self.session.refresh(entry)
        return entry

    # ---------- 用户偏好 ----------

    async def get_preference(self, user_id: str) -> Optional[UserFoodPreference]:
        result = await self.session.execute(
            select(UserFoodPreference).where(UserFoodPreference.user_id == user_id)
        )
        return result.scalar_one_or_none()

    async def upsert_preference(self, user_id: str, **kwargs) -> UserFoodPreference:
        pref = await self.get_preference(user_id)
        if pref is None:
            pref = UserFoodPreference(id=str(uuid.uuid4()), user_id=user_id)
            self.session.add(pref)

        for key, value in kwargs.items():
            if hasattr(pref, key):
                setattr(pref, key, value)

        await self.session.commit()
        await self.session.refresh(pref)
        return pref

    # ---------- 统计分析 ----------

    async def get_daily_summary(self, user_id: str, target_date: date) -> dict:
        """某天的营养摄入汇总。"""
        items = await self.get_log_items_by_date(user_id, target_date)
        return _summarize_logs(items, target_date, target_date)

    async def get_weekly_summary(self, user_id: str, week_start: date) -> dict:
        """某周营养汇总 + 每日趋势。"""
        week_end = week_start + timedelta(days=6)
        items = await self.get_log_items_by_range(user_id, week_start, week_end)
        summary = _summarize_logs(items, week_start, week_end)

        # 每日趋势
        daily = []
        for i in range(7):
            day = week_start + timedelta(days=i)
            day_items = [it for it in items if it.log_date == day]
            day_sum = _summarize_logs(day_items, day, day)
            daily.append({"date": day.isoformat(), **day_sum["totals"]})

        summary["daily"] = daily
        return summary

    async def get_plan_vs_actual_deviation(
        self, user_id: str, week_start: date
    ) -> dict:
        """计划 vs 实际偏差分析。"""
        week_end = week_start + timedelta(days=6)
        plan_meals = await self.get_plan_meals_by_week(user_id, week_start)
        log_items = await self.get_log_items_by_range(user_id, week_start, week_end)

        # 计划的每天每餐热量
        plan_map = {}
        for meal in plan_meals:
            key = (meal.plan_date.isoformat(), meal.meal_type)
            plan_map[key] = {
                "calories": meal.total_calories or 0,
                "protein": meal.total_protein or 0,
            }

        # 实际摄入的每天每餐热量（按 log 聚合）
        actual_map = {}
        for item in log_items:
            key = (item.log_date.isoformat(), item.meal_type)
            entry = actual_map.setdefault(key, {"calories": 0, "protein": 0})
            entry["calories"] += item.calories or 0
            entry["protein"] += item.protein or 0

        # 逐餐对比
        per_meal = []
        processed = set()
        all_keys = set(plan_map) | set(actual_map)
        for key in sorted(all_keys):
            date_str, meal_type = key
            plan = plan_map.get(key, {"calories": 0, "protein": 0})
            actual = actual_map.get(key, {"calories": 0, "protein": 0})
            dev_pct = (
                (actual["calories"] - plan["calories"]) / plan["calories"]
                if plan["calories"] > 0 and actual["calories"] > 0
                else None
            )
            per_meal.append({
                "date": date_str,
                "meal_type": meal_type,
                "plan_calories": plan["calories"],
                "actual_calories": actual["calories"],
                "deviation_pct": dev_pct,
            })
            processed.add(date_str)

        # 汇总
        plan_total = sum(entry["calories"] for entry in plan_map.values())
        actual_total = sum(entry["calories"] for entry in actual_map.values())

        return {
            "week_start": week_start.isoformat(),
            "plan_total_calories": plan_total,
            "actual_total_calories": actual_total,
            "total_deviation_pct": (
                (actual_total - plan_total) / plan_total
                if plan_total > 0 else None
            ),
            "execution_rate": (
                min(1.0, actual_total / plan_total) if plan_total > 0 else None
            ),
            "per_meal": per_meal,
        }


# ============================================================
# 内部工具函数
# ============================================================

def _sum_dishes(dishes: Optional[list]) -> dict:
    """汇总菜品列表的营养总量。"""
    totals = {"calories": None, "protein": None, "fat": None, "carbs": None}
    if not dishes:
        return totals

    cal = sum(d.get("calories") or 0 for d in dishes if isinstance(d, dict))
    pro = sum(d.get("protein") or 0 for d in dishes if isinstance(d, dict))
    fat = sum(d.get("fat") or 0 for d in dishes if isinstance(d, dict))
    carb = sum(d.get("carbs") or 0 for d in dishes if isinstance(d, dict))

    if cal:
        totals["calories"] = cal
    if pro:
        totals["protein"] = pro
    if fat:
        totals["fat"] = fat
    if carb:
        totals["carbs"] = carb
    return totals


def _summarize_logs(items: List[DietLogItem], start: date, end: date) -> dict:
    """聚合一组饮食记录的营养总量与餐次分布。"""
    totals = {"calories": 0.0, "protein": 0.0, "fat": 0.0, "carbs": 0.0}
    for it in items:
        totals["calories"] += it.calories or 0
        totals["protein"] += it.protein or 0
        totals["fat"] += it.fat or 0
        totals["carbs"] += it.carbs or 0

    by_meal = {}
    for it in items:
        m = by_meal.setdefault(it.meal_type, {"calories": 0.0, "protein": 0.0, "fat": 0.0, "carbs": 0.0})
        m["calories"] += it.calories or 0
        m["protein"] += it.protein or 0
        m["fat"] += it.fat or 0
        m["carbs"] += it.carbs or 0

    return {
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "totals": {k: round(v, 1) for k, v in totals.items()},
        "meal_breakdown": by_meal,
        "log_count": len(items),
    }