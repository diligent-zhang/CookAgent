"""
饮食模块 API 请求/响应模型
"""
from datetime import date
from typing import List, Optional

from pydantic import BaseModel, Field


class DishSchema(BaseModel):
    """计划餐次中的一个菜品。"""
    name: str = Field(..., description="菜品名称")
    weight_g: Optional[float] = Field(None, description="重量(克)")
    unit: Optional[str] = Field(None, description="单位（份/个/碗等）")
    calories: Optional[float] = Field(None, description="卡路里")
    protein: Optional[float] = Field(None, description="蛋白质(克)")
    fat: Optional[float] = Field(None, description="脂肪(克)")
    carbs: Optional[float] = Field(None, description="碳水化合物(克)")


class AddMealRequest(BaseModel):
    plan_date: date = Field(..., description="计划日期 YYYY-MM-DD")
    meal_type: str = Field(..., description="餐次类型 breakfast/lunch/dinner/snack")
    dishes: Optional[List[DishSchema]] = Field(None, description="菜品列表")
    notes: Optional[str] = Field(None, description="备注")


class UpdateMealRequest(BaseModel):
    dishes: Optional[List[DishSchema]] = None
    notes: Optional[str] = None


class CopyMealRequest(BaseModel):
    target_date: date = Field(..., description="目标日期 YYYY-MM-DD")
    target_meal_type: Optional[str] = Field(None, description="目标餐次类型")


class FoodItemSchema(BaseModel):
    """饮食记录中的食物项。"""
    food_name: str = Field(..., description="食物名称")
    weight_g: Optional[float] = None
    unit: Optional[str] = None
    calories: Optional[float] = None
    protein: Optional[float] = None
    fat: Optional[float] = None
    carbs: Optional[float] = None
    source: Optional[str] = Field(None, description="manual/ai_text/ai_image")


class CreateLogRequest(BaseModel):
    log_date: date = Field(..., description="记录日期")
    meal_type: str = Field(..., description="餐次类型")
    items: Optional[List[FoodItemSchema]] = Field(None, description="食物列表")
    plan_meal_id: Optional[str] = Field(None, description="关联计划餐次 ID")
    notes: Optional[str] = Field(None)


class LogFromTextRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=1000, description="饮食描述")
    log_date: Optional[date] = Field(None, description="记录日期（默认今天）")
    meal_type: Optional[str] = Field(None, description="餐次类型（可自动推断）")


class UpdatePreferenceRequest(BaseModel):
    dietary_restrictions: Optional[List[str]] = Field(None, description="饮食限制")
    allergies: Optional[List[str]] = Field(None, description="过敏原")
    favorite_cuisines: Optional[List[str]] = Field(None, description="喜爱的菜系")
    avoided_foods: Optional[List[str]] = Field(None, description="不喜欢的食物")
    disliked_foods: Optional[List[str]] = Field(None, description="不喜欢的食物（兼容字段）")
    calorie_goal: Optional[int] = Field(None, description="每日卡路里目标")
    protein_goal: Optional[float] = Field(None, description="每日蛋白质目标(克)")
    fat_goal: Optional[float] = Field(None, description="每日脂肪目标(克)")
    carbs_goal: Optional[float] = Field(None, description="每日碳水目标(克)")