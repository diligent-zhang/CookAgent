"""
饮食管理 Tool：制定饮食计划、记录饮食、营养分析

这些工具把 DietService（数据库持久化）能力暴露给 Agent，
让 Agent 能：
  - diet_plan:        为用户制定/查看/修改一周饮食计划（持久化到 DB）
  - diet_log:         记录吃过的食物（支持 AI 解析自然语言）
  - diet_analysis:    日/周营养汇总、计划 vs 实际偏差分析

与 meal_plan_tools（生成 Markdown 文本计划）的区别：
  meal_plan_tools 偏"一次性生成文本计划"；
  diet_tools 偏"持久化的饮食管理"（计划存库、记录存库、可统计分析）。
"""
import json
from datetime import date
from typing import Optional

from langchain_core.tools import tool

from app.config import settings

# 运行时注入的依赖
_llm_provider = None


def set_diet_tools_deps(llm_provider=None):
    """运行时注入依赖（避免循环导入）。"""
    global _llm_provider
    _llm_provider = llm_provider


def _get_service(db):
    """创建一个绑定 session 的 DietService。"""
    from app.diet.service import DietService
    return DietService(db)


def _date_from_str(value: Optional[str], default: Optional[date] = None) -> Optional[date]:
    if not value:
        return default
    try:
        return date.fromisoformat(value)
    except ValueError:
        return default


# ════════════════════════════════════════════════════════════════════
# 饮食计划工具
# ════════════════════════════════════════════════════════════════════

@tool
async def diet_plan(
    action: str,
    user_id: str,
    week_start_date: Optional[str] = None,
    plan_date: Optional[str] = None,
    meal_type: Optional[str] = None,
    dishes: Optional[list] = None,
    notes: Optional[str] = None,
    meal_id: Optional[str] = None,
    target_date: Optional[str] = None,
) -> str:
    """
    管理用户的饮食计划。按周规划每天三餐 + 加餐，数据持久化保存。

    适用场景：
    - 用户想让 AI 帮忙安排一周三餐
    - 用户想查看/修改某一天的饮食安排
    - 用户想复制某天的餐到另一天复用

    参数：
        action: 操作类型，必须为以下之一：
          - "get_by_week": 查看某周计划（需 week_start_date，周一日期）
          - "add_meal": 添加/更新一餐（需 plan_date + meal_type + dishes）
          - "update_meal": 修改某餐（需 meal_id，可选 dishes/notes）
          - "delete_meal": 删除某餐（需 meal_id）
          - "copy_meal": 复制一餐到另一天（需 meal_id + target_date）
          - "mark_eaten": 标记计划餐已吃，自动转为饮食记录（需 meal_id）
        user_id: 用户 ID (系统自动提供)
        week_start_date: 周一日期，格式 YYYY-MM-DD
        plan_date: 计划日期，格式 YYYY-MM-DD
        meal_type: 餐次类型: breakfast/lunch/dinner/snack
        dishes: 菜品列表，如 [{"name":"清蒸鲈鱼","calories":280,"protein":35},
                            {"name":"蒜蓉西兰花","calories":80}]
        notes: 备注
        meal_id: 餐次 ID
        target_date: 复制目标日期 YYYY-MM-DD
    """
    from app.database.session import AsyncSessionLocal

    async with AsyncSessionLocal() as db:
        service = _get_service(db)
        try:
            if action == "get_by_week":
                ws = _date_from_str(week_start_date)
                if not ws:
                    return "错误：查看周计划需要 week_start_date（周一日期）。"
                plan = await service.get_plan_by_week(user_id, ws)
                return json.dumps(plan, ensure_ascii=False, default=str)

            elif action == "add_meal":
                if not plan_date or not meal_type:
                    return "错误：添加餐次需要 plan_date 和 meal_type。"
                if meal_type not in ("breakfast", "lunch", "dinner", "snack"):
                    return f"错误：无效餐次类型 {meal_type}，应为 breakfast/lunch/dinner/snack。"
                pd = _date_from_str(plan_date)
                if not pd:
                    return f"错误：无法解析日期 {plan_date}。"
                meal = await service.add_meal(
                    user_id, pd, meal_type, dishes=dishes or [], notes=notes
                )
                return json.dumps({"status": "success", "meal": meal}, ensure_ascii=False, default=str)

            elif action == "update_meal":
                if not meal_id:
                    return "错误：修改餐次需要 meal_id。"
                meal = await service.update_meal(
                    meal_id, user_id,
                    dishes=dishes if dishes is not None else None,
                    notes=notes,
                )
                if meal is None:
                    return "错误：餐次不存在或无权访问。"
                return json.dumps({"status": "success", "meal": meal}, ensure_ascii=False, default=str)

            elif action == "delete_meal":
                if not meal_id:
                    return "错误：删除餐次需要 meal_id。"
                ok = await service.delete_meal(meal_id, user_id)
                return "餐次已删除。" if ok else "错误：餐次不存在或无权访问。"

            elif action == "copy_meal":
                if not meal_id or not target_date:
                    return "错误：复制餐次需要 meal_id 和 target_date。"
                td = _date_from_str(target_date)
                if not td:
                    return f"错误：无法解析日期 {target_date}。"
                meal = await service.copy_meal(meal_id, user_id, td)
                if meal is None:
                    return "错误：餐次不存在或无权访问。"
                return json.dumps({"status": "success", "meal": meal}, ensure_ascii=False, default=str)

            elif action == "mark_eaten":
                if not meal_id:
                    return "错误：标记已吃需要 meal_id。"
                log = await service.mark_plan_meal_as_eaten(user_id, meal_id)
                if log is None:
                    return "错误：餐次不存在或无权访问。"
                return json.dumps({"status": "success", "log": log}, ensure_ascii=False, default=str)

            return f"错误：未知操作 {action}。可选：get_by_week/add_meal/update_meal/delete_meal/copy_meal/mark_eaten"
        except Exception as e:
            return f"饮食计划操作失败: {e}"


# ════════════════════════════════════════════════════════════════════
# 饮食记录工具
# ════════════════════════════════════════════════════════════════════

@tool
async def diet_log(
    action: str,
    user_id: str,
    description: Optional[str] = None,
    log_date: Optional[str] = None,
    meal_type: Optional[str] = None,
    items: Optional[list] = None,
    log_id: Optional[str] = None,
) -> str:
    """
    记录用户每天吃了什么，并自动估算热量和营养。

    亮点是 "log_from_text"：用户说"中午吃了半碗米饭配红烧肉，还有一杯奶茶"，
    AI 自动解析出食物清单和热量，无需手动填写。

    适用场景：
    - 用户想记录今天的饮食
    - 用户想追踪每日热量摄入
    - 用户汇报了吃的食物后需要 AI 帮忙计算热量

    参数：
        action: 操作类型：
          - "log_from_text": AI 解析自然语言描述记录（推荐，需 description）
          - "log_manual": 手动记录（需 items 列表 + log_date + meal_type）
          - "get_by_date": 查看某天记录（需 log_date）
          - "delete": 删除一条记录（需 log_id）
        user_id: 用户 ID (系统自动提供)
        description: 饮食描述，如 "早餐吃了两个鸡蛋和一杯牛奶"
        log_date: 日期 YYYY-MM-DD（默认今天）
        meal_type: 餐次类型 breakfast/lunch/dinner/snack（AI 会尽量推断）
        items: 手动记录时的食物列表 [{"food_name":"米饭","weight_g":200,"calories":230}]
        log_id: 删除时的记录 ID
    """
    from app.database.session import AsyncSessionLocal

    async with AsyncSessionLocal() as db:
        service = _get_service(db)
        try:
            if action == "log_from_text":
                if not description:
                    return "错误：AI 记录需要 description（饮食描述）。"
                if settings.diet_ai_parsing_enabled and _llm_provider is None:
                    return "错误：LLM 服务未初始化，无法解析饮食描述。请使用 log_manual。"
                log = await service.log_from_text(
                    user_id,
                    description,
                    log_date=_date_from_str(log_date),
                    meal_type=meal_type,
                    llm_provider=_llm_provider,
                )
                return json.dumps({"status": "success", "log": log}, ensure_ascii=False, default=str)

            elif action == "log_manual":
                if not items:
                    return "错误：手动记录需要 items（食物列表）。"
                ld = _date_from_str(log_date, default=date.today())
                log = await service.log_manual(
                    user_id, ld, meal_type or "snack", items=items
                )
                return json.dumps({"status": "success", "log": log}, ensure_ascii=False, default=str)

            elif action == "get_by_date":
                ld = _date_from_str(log_date, default=date.today())
                logs = await service.get_logs_by_date(user_id, ld)
                return json.dumps({"logs": logs}, ensure_ascii=False, default=str)

            elif action == "delete":
                if not log_id:
                    return "错误：删除记录需要 log_id。"
                ok = await service.delete_log(user_id, log_id)
                return "记录已删除。" if ok else "错误：记录不存在或无权访问。"

            return f"错误：未知操作 {action}。可选：log_from_text/log_manual/get_by_date/delete"
        except Exception as e:
            return f"饮食记录操作失败: {e}"


# ════════════════════════════════════════════════════════════════════
# 营养分析工具
# ════════════════════════════════════════════════════════════════════

@tool
async def diet_analysis(
    action: str,
    user_id: str,
    target_date: Optional[str] = None,
    week_start_date: Optional[str] = None,
) -> str:
    """
    分析用户的营养摄入数据，生成日/周营养报告和计划执行情况。

    适用场景：
    - 用户想知道今天摄入了多少热量
    - 用户想回顾一周营养概况
    - 用户想查看计划执行率或饮食偏差
    - 用户想基于数据获得营养建议

    参数：
        action: 操作类型：
          - "daily_summary": 日汇总（需 target_date，默认今天）
          - "weekly_summary": 周汇总（需 week_start_date，默认本周）
          - "deviation": 计划 vs 实际偏差分析（需 week_start_date，默认本周）
          - "preferences": 查看用户饮食偏好
        user_id: 用户 ID (系统自动提供)
        target_date: 日期 YYYY-MM-DD
        week_start_date: 周一日期 YYYY-MM-DD
    """
    from app.database.session import AsyncSessionLocal

    async with AsyncSessionLocal() as db:
        service = _get_service(db)
        try:
            if action == "daily_summary":
                td = _date_from_str(target_date, default=date.today())
                return json.dumps(await service.get_daily_summary(user_id, td), ensure_ascii=False, default=str)

            elif action == "weekly_summary":
                ws = _date_from_str(week_start_date)
                return json.dumps(await service.get_weekly_summary(user_id, ws), ensure_ascii=False, default=str)

            elif action == "deviation":
                ws = _date_from_str(week_start_date)
                return json.dumps(await service.get_deviation_analysis(user_id, ws), ensure_ascii=False, default=str)

            elif action == "preferences":
                pref = await service.get_user_preference(user_id)
                return json.dumps({"preference": pref}, ensure_ascii=False, default=str)

            return f"错误：未知操作 {action}。可选：daily_summary/weekly_summary/deviation/preferences"
        except Exception as e:
            return f"营养分析操作失败: {e}"