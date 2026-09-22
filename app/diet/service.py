"""
DietService — 饮食管理核心业务层。

同时被 REST API（app.diet.router）和 Agent 工具（app.agent.tools.diet_tools）
使用。所有方法都传入 AsyncSession，由调用方管理事务。

核心能力：
  1. 计划管理：按周查看/添加/更新/删除/复制计划餐次
  2. 记录管理：手动记录 + AI 自然语言解析记录（自动估算热量）
  3. 营养分析：日/周汇总、计划 vs 实际偏差
  4. 用户偏好：查看/更新饮食偏好
"""
import json
import logging
from datetime import date, datetime
from typing import List, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.diet.models import DataSource, MealType
from app.diet.repository import DietRepository, get_week_start

logger = logging.getLogger(__name__)

# AI 解析饮食描述的系统提示词
DIET_LOG_SYSTEM_PROMPT = """你是一个营养师助手。将用户的饮食描述解析为结构化食物列表。
只返回 JSON，格式：
{"meal_type": "breakfast|lunch|dinner|snack", "items": [{"food_name": "食物名", "weight_g": 150, "unit": "份/克/个/碗", "calories": 350, "protein": 15, "fat": 10, "carbs": 40}]}
注意：
- calories 是估算千卡数，protein/fat/carbs 是克数
- 如果无法确定某字段，用 null
- 只输出 JSON，不要解释"""

DIET_LOG_PARSE_PROMPT = "解析以下饮食记录：\n{text}"


class DietService:
    """饮食管理核心业务服务。"""

    def __init__(self, db: AsyncSession):
        self.db = db
        self.repo = DietRepository(db)

    # ==================== 计划管理 ====================

    async def get_plan_by_week(self, user_id: str, week_start: date) -> dict:
        """获取某周计划（按天+餐次组织）。"""
        meals = await self.repo.get_plan_meals_by_week(user_id, week_start)
        return {
            "week_start": week_start.isoformat(),
            "meals": [m.to_dict() for m in meals],
        }

    async def add_meal(
        self,
        user_id: str,
        plan_date: date,
        meal_type: str,
        dishes: Optional[list] = None,
        notes: Optional[str] = None,
    ) -> dict:
        """添加/更新一顿计划餐。dishes 自动汇总营养。"""
        if meal_type not in MealType.ALL:
            raise ValueError(f"无效餐次类型: {meal_type}")
        meal = await self.repo.upsert_plan_meal(
            user_id, plan_date, meal_type, dishes=dishes, notes=notes
        )
        return meal.to_dict()

    async def update_meal(self, meal_id: str, user_id: str, **kwargs) -> Optional[dict]:
        meal = await self.repo.get_plan_meal(meal_id)
        if meal is None or str(meal.user_id) != str(user_id):
            return None
        return (await self.repo.update_plan_meal(meal_id, **kwargs)).to_dict()

    async def delete_meal(self, meal_id: str, user_id: str) -> bool:
        meal = await self.repo.get_plan_meal(meal_id)
        if meal is None or str(meal.user_id) != str(user_id):
            return False
        return await self.repo.delete_plan_meal(meal_id)

    async def copy_meal(
        self,
        meal_id: str,
        user_id: str,
        target_date: date,
        target_meal_type: Optional[str] = None,
    ) -> Optional[dict]:
        meal = await self.repo.get_plan_meal(meal_id)
        if meal is None or str(meal.user_id) != str(user_id):
            return None
        new = await self.repo.copy_plan_meal(meal_id, target_date, target_meal_type)
        return new.to_dict()

    # ==================== 记录管理 ====================

    async def log_manual(
        self,
        user_id: str,
        log_date: date,
        meal_type: str,
        items: Optional[List[dict]] = None,
        notes: Optional[str] = None,
        plan_meal_id: Optional[str] = None,
    ) -> dict:
        """手动记录饮食。"""
        if meal_type not in MealType.ALL:
            meal_type = MealType.SNACK
        normalized = [
            {**it, "source": DataSource.MANUAL}
            for it in (items or [{"food_name": "未记录食物"}]) if isinstance(it, dict)
        ]
        created = await self.repo.create_log_items(
            user_id, log_date, meal_type, normalized,
            notes=notes, plan_meal_id=plan_meal_id,
        )
        return _build_log_dict(created)

    async def log_from_text(
        self,
        user_id: str,
        text: str,
        log_date: Optional[date] = None,
        meal_type: Optional[str] = None,
        llm_provider=None,
        image_text: Optional[str] = None,
    ) -> dict:
        """
        AI 解析自然语言饮食描述并记录。★ 核心功能。

        示例："中午吃了半碗米饭配红烧肉" → 结构化存储热量。
        解析失败时降级为"把整段文本当食物名"。
        """
        parsed = None
        used_vision = False

        # 图片识别结果作为附加输入
        try:
            if llm_provider is not None:
                invoker = llm_provider.create_invoker("fast", streaming=False)
                prompt_text = text
                if image_text:
                    prompt_text = f"{text}（图片识别结果：{image_text}）"
                    used_vision = True
                response = await invoker.ainvoke([
                    {"role": "system", "content": DIET_LOG_SYSTEM_PROMPT},
                    {"role": "user", "content": DIET_LOG_PARSE_PROMPT.format(text=prompt_text)},
                ])
                content = response.content if hasattr(response, "content") else str(response)
                parsed = _parse_ai_json(content)
        except Exception as e:
            logger.warning("AI diet parsing failed, fallback to manual: %s", e)

        # 从解析结果或降级提取
        items = []
        actual_meal = meal_type
        if parsed and parsed.get("items"):
            actual_meal = parsed.get("meal_type") or meal_type or MealType.SNACK
            if isinstance(actual_meal, list):
                actual_meal = actual_meal[0] if actual_meal else MealType.SNACK
            items = [
                {
                    "food_name": it.get("food_name", "未知食物"),
                    "weight_g": it.get("weight_g"),
                    "unit": it.get("unit"),
                    "calories": it.get("calories"),
                    "protein": it.get("protein"),
                    "fat": it.get("fat"),
                    "carbs": it.get("carbs"),
                    "source": DataSource.AI_IMAGE if used_vision else DataSource.AI_TEXT,
                }
                for it in parsed["items"]
            ]

        if not items:
            # 降级：整段文本作为一条手动记录
            items = [{
                "food_name": text[:100],
                "source": DataSource.AI_TEXT,
            }]
            actual_meal = meal_type or MealType.SNACK

        created = await self.repo.create_log_items(
            user_id,
            log_date or date.today(),
            actual_meal,
            items,
            notes=f"AI 解析:{text[:200]}",
        )
        return _build_log_dict(created)

    async def mark_plan_meal_as_eaten(
        self,
        user_id: str,
        plan_meal_id: str,
        log_date: Optional[date] = None,
    ) -> Optional[dict]:
        """把计划中的一餐标记为已吃，自动转为饮食记录。"""
        meal = await self.repo.get_plan_meal(plan_meal_id)
        if meal is None or str(meal.user_id) != str(user_id):
            return None

        dishes = meal.dishes or []
        items = []
        for dish in dishes:
            if not isinstance(dish, dict):
                continue
            items.append({
                "food_name": dish.get("name", "未知食物"),
                "weight_g": dish.get("weight_g"),
                "unit": dish.get("unit"),
                "calories": dish.get("calories"),
                "protein": dish.get("protein"),
                "fat": dish.get("fat"),
                "carbs": dish.get("carbs"),
                "source": DataSource.MANUAL,
            })

        return await self.log_manual(
            user_id=user_id,
            log_date=log_date or date.today(),
            meal_type=meal.meal_type,
            items=items,
            plan_meal_id=plan_meal_id,
        )

    async def get_logs_by_date(self, user_id: str, log_date: date) -> list:
        """查询某天的所有记录（按 log_id 分组）。"""
        items = await self.repo.get_log_items_by_date(user_id, log_date)
        return _group_logs(items)

    async def get_logs_by_range(self, user_id: str, start: date, end: date) -> list:
        items = await self.repo.get_log_items_by_range(user_id, start, end)
        return _group_logs(items)

    async def delete_log(self, user_id: str, log_id: str) -> bool:
        items = await self.repo.get_log_items_by_log_id(log_id)
        if not items or str(items[0].user_id) != str(user_id):
            return False
        return await self.repo.delete_log(log_id)

    # ==================== 营养分析 ====================

    async def get_daily_summary(self, user_id: str, target_date: date) -> dict:
        """某天的营养汇总。"""
        return await self.repo.get_daily_summary(user_id, target_date)

    async def get_weekly_summary(
        self, user_id: str, week_start: Optional[date] = None
    ) -> dict:
        """某周营养汇总 + 每日趋势。"""
        week_start = week_start or get_week_start(date.today())
        return await self.repo.get_weekly_summary(user_id, week_start)

    async def get_deviation_analysis(
        self, user_id: str, week_start: Optional[date] = None
    ) -> dict:
        """计划 vs 实际偏差分析。"""
        week_start = week_start or get_week_start(date.today())
        return await self.repo.get_plan_vs_actual_deviation(user_id, week_start)

    # ==================== 用户偏好 ====================

    async def get_user_preference(self, user_id: str) -> Optional[dict]:
        pref = await self.repo.get_preference(user_id)
        return pref.to_dict() if pref else None

    async def update_user_preference(self, user_id: str, **kwargs) -> dict:
        """更新偏好。兼容 disliked_foods → avoided_foods。"""
        if "disliked_foods" in kwargs and "avoided_foods" not in kwargs:
            kwargs["avoided_foods"] = kwargs.pop("disliked_foods")
        else:
            kwargs.pop("disliked_foods", None)
        pref = await self.repo.upsert_preference(user_id, **kwargs)
        return pref.to_dict()


# ============================================================
# 内部辅助
# ============================================================

def _parse_ai_json(content: str) -> Optional[dict]:
    """从 LLM 输出中提取 JSON（容错 markdown 代码块包围）。"""
    if not content:
        return None
    text = content.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1] if "\n" in text else text
        text = text.rsplit("```", 1)[0] if "```" in text.split("\n", 1)[-1] else text
        text = text.strip()
    # 去掉 ``` 代码块标记
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        logger.warning("AI JSON parse failed: %s", text[:200])
        return None


def _build_log_dict(items: List) -> dict:
    """将同一 log 的多条 food_item 聚合为一条日志。"""
    if not items:
        return {}
    first = items[0]
    total = {k: round(sum((getattr(it, k) or 0) for it in items), 1) for k in
             ("calories", "protein", "fat", "carbs")}
    return {
        "log_id": str(first.log_id),
        "log_date": first.log_date.isoformat(),
        "meal_type": first.meal_type,
        "items": [it.to_dict() for it in items],
        "total_calories": total["calories"] or None,
        "total_protein": total["protein"] or None,
        "total_fat": total["fat"] or None,
        "total_carbs": total["carbs"] or None,
        "notes": first.notes,
        "created_at": first.created_at.isoformat(),
    }


def _group_logs(items: List) -> list:
    """按 log_id 分组多条记录。"""
    grouped: dict = {}
    for it in items:
        grouped.setdefault(str(it.log_id), []).append(it)
    return [_build_log_dict(v) for v in grouped.values()]