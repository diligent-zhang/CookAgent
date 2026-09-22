from app.agent.tools.recipe_tools import (
    search_recipes,
    get_recipe_detail,
    set_rag_service as _set_recipe_rag,
)
from app.agent.tools.user_tools import (
    get_user_dietary_info,
    check_ingredient_safety,
    set_rag_service as _set_user_rag,
)
from app.agent.tools.nutrition_tools import (
    estimate_calories,
    suggest_substitutes,
)
from app.agent.tools.meal_plan_tools import (
    plan_weekly_meals,
    export_meal_plan,
    export_meal_plan_ics,
    export_meal_plan_html,
    set_meal_plan_deps,
)
from app.agent.tools.diet_tools import (
    diet_plan,
    diet_log,
    diet_analysis,
    set_diet_tools_deps as _set_diet_tools_deps,
)
from app.agent.tools.common_tools import (
    get_current_datetime,
    calculator,
)
from app.agent.tools.web_search import web_search


def get_all_tools():
    """返回 Agent 可用的所有工具列表"""
    return [
        # 菜谱工具
        search_recipes,
        get_recipe_detail,
        # 用户工具
        get_user_dietary_info,
        check_ingredient_safety,
        # 营养工具
        estimate_calories,
        suggest_substitutes,
        # 饮食规划工具（一次性文本生成 + 导出）
        plan_weekly_meals,
        export_meal_plan,
        export_meal_plan_ics,
        export_meal_plan_html,
        # 饮食管理工具（持久化：计划/记录/分析）
        diet_plan,
        diet_log,
        diet_analysis,
        # 常用工具
        get_current_datetime,
        calculator,
        # Web 搜索工具
        web_search,
    ]


def inject_rag_service(rag_service):
    """将 RAG 服务注入到工具函数中（解决循环导入）。"""
    _set_recipe_rag(rag_service)
    _set_user_rag(rag_service)


def inject_meal_plan_deps(rag_service, llm_provider, storage_dir=None):
    """将 RAG、LLM 服务注入到饮食规划工具中。"""
    set_meal_plan_deps(rag_service, llm_provider, storage_dir)


def inject_diet_tools_deps(llm_provider=None):
    """将 LLM 服务注入到饮食管理工具中（用于 AI 文字解析）。"""
    _set_diet_tools_deps(llm_provider)
