"""
用户相关 Tool：查询和更新用户饮食偏好

 用户偏好存储在 user 表的扩展字段或独立表中。
 这里用 user_preferences 字典模拟（后续可改为独立的 preferences 表）。
"""
from typing import Optional

from langchain_core.tools import tool
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import User

# 运行时注入 _rag_service
_rag_service = None


def set_rag_service(rag_service):
    global _rag_service
    _rag_service = rag_service


@tool
async def get_user_dietary_info(user_id: str) -> str:
    """
    获取用户的饮食偏好和限制。在推荐菜谱或回答饮食相关问题时必须先调用此工具，
    确保推荐符合用户的口味偏好、饮食限制和过敏信息。

    返回用户的饮食偏好信息，包括：口味偏好、饮食类型、过敏食材等。

    参数：
        user_id: 用户 ID
    """
    # 这里用简化的方式：从用户信息中提取偏好
    # 生产环境建议建一张 user_preferences 表
    from app.database.session import AsyncSessionLocal

    async with AsyncSessionLocal() as db:
        result = await db.execute(select(User).where(User.id == user_id))
        user = result.scalar_one_or_none()

    if user is None:
        return "用户不存在"

    # 用 nickname 作为简单偏好存储的占位
    # 真实实现应该有关联的 UserPreference 表
    return (
        f"用户信息：\n"
        f"- 用户名: {user.username}\n"
        f"- 昵称: {user.nickname or '未设置'}\n"
        f"- 注册时间: {user.created_at.strftime('%Y-%m-%d')}\n"
        f"注意：详细的饮食偏好（过敏食材、饮食类型等）需要在个人设置中填写。"
    )


@tool
async def check_ingredient_safety(dish_name: str, allergies: str) -> str:
    """
    检查某道菜的食材是否与用户的过敏食材有冲突。

    当用户表达了对某道菜的兴趣，但有过过敏史时，调用此工具进行检查。

    参数：
        dish_name: 要检查的菜名
        allergies: 用户的过敏食材，用逗号分隔，如"花生,虾,牛奶"
    """
    if _rag_service is None:
        return "错误：菜谱检索服务未初始化"

    try:
        docs = await _rag_service.retrieve(query=dish_name, top_k=1)
    except Exception as e:
        return f"检查食材安全时出错: {e}"

    if not docs:
        return f"没有找到「{dish_name}」的菜谱，无法检查食材安全。"

    recipe_text = docs[0].page_content
    allergy_list = [a.strip() for a in allergies.split(",") if a.strip()]

    found_allergens = []
    for allergen in allergy_list:
        if allergen in recipe_text:
            found_allergens.append(allergen)

    if found_allergens:
        return (
            f"⚠️ 警告：「{dish_name}」的菜谱中含有以下过敏食材："
            f"{'、'.join(found_allergens)}\n"
            f"建议：寻找不含这些食材的替代菜谱，或考虑用其他食材替换。"
        )
    else:
        return (
            f"✓ 安全：「{dish_name}」的菜谱中未发现与过敏清单 "
            f"({', '.join(allergy_list)}) 冲突的食材。"
        )
