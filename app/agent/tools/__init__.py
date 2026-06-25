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
    ]


def inject_rag_service(rag_service):
    """将 RAG 服务注入到工具函数中（解决循环导入）。"""
    _set_recipe_rag(rag_service)
    _set_user_rag(rag_service)
