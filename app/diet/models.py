"""
饮食模块 ORM 模型

包含饮食计划餐次、饮食记录食品项和用户饮食偏好三张表。
复用 app.database.models 中的 Base 与 generate_uuid。
"""
from datetime import datetime

from sqlalchemy import (
    Column,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.database.models import generate_uuid
from app.database.session import Base


# ============================================================
# 枚举定义（存储为字符串，避免 ENUM 迁移问题）
# ============================================================

class MealType:
    """餐次类型常量"""
    BREAKFAST = "breakfast"
    LUNCH = "lunch"
    DINNER = "dinner"
    SNACK = "snack"

    ALL = {BREAKFAST, LUNCH, DINNER, SNACK}


class DataSource:
    """数据来源常量"""
    MANUAL = "manual"        # 手动录入
    AI_TEXT = "ai_text"      # AI 自然语言解析
    AI_IMAGE = "ai_image"    # AI 图片识别


# ============================================================
# 表 1：饮食计划餐次（某一周某一天某一餐的计划）
# ============================================================

class DietPlanMeal(Base):
    """
    饮食计划中的一餐。
    同一用户同一天同一餐次只有一条记录（upsert）。
    disputes 里存菜品列表 JSON：[{"name", "calories", "protein", ...}]
    """
    __tablename__ = "diet_plan_meals"

    id = Column(UUID(as_uuid=False), primary_key=True, default=generate_uuid)
    user_id = Column(
        UUID(as_uuid=False), ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    plan_date = Column(Date, nullable=False, index=True)
    meal_type = Column(String(20), nullable=False)  # breakfast/lunch/dinner/snack
    dishes = Column(JSON, nullable=True)            # [{name, calories, protein, fat, carbs, unit, weight_g}]
    total_calories = Column(Float, nullable=True)
    total_protein = Column(Float, nullable=True)
    total_fat = Column(Float, nullable=True)
    total_carbs = Column(Float, nullable=True)
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        Index("ix_diet_plan_user_date", "user_id", "plan_date"),
        Index("ix_diet_plan_user_date_meal", "user_id", "plan_date", "meal_type"),
    )

    def to_dict(self, include_id=True):
        data = {
            "plan_date": self.plan_date.isoformat(),
            "meal_type": self.meal_type,
            "dishes": self.dishes or [],
            "total_calories": self.total_calories,
            "total_protein": self.total_protein,
            "total_fat": self.total_fat,
            "total_carbs": self.total_carbs,
            "notes": self.notes,
        }
        if include_id:
            data["id"] = str(self.id)
        return data


# ============================================================
# 表 2：饮食记录食品项（用户实际吃了什么）
# ============================================================

class DietLogItem(Base):
    """
    饮食记录中的一条食物。
    同一次记录（log_id）可包含多条 food_item。
    """
    __tablename__ = "diet_log_items"

    id = Column(UUID(as_uuid=False), primary_key=True, default=generate_uuid)
    # 同一次记录的多条食物共享一个 log_id，便于按"一顿饭"聚合
    log_id = Column(UUID(as_uuid=False), nullable=False, index=True)
    user_id = Column(
        UUID(as_uuid=False), ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    log_date = Column(Date, nullable=False, index=True)
    meal_type = Column(String(20), nullable=False)  # breakfast/lunch/dinner/snack
    # 若由"已吃"计划餐次生成，关联到计划餐
    plan_meal_id = Column(
        UUID(as_uuid=False), ForeignKey("diet_plan_meals.id", ondelete="SET NULL"),
        nullable=True,
    )
    food_name = Column(String(255), nullable=False)
    weight_g = Column(Float, nullable=True)
    unit = Column(String(50), nullable=True)
    calories = Column(Float, nullable=True)
    protein = Column(Float, nullable=True)
    fat = Column(Float, nullable=True)
    carbs = Column(Float, nullable=True)
    # 数据来源：manual / ai_text / ai_image
    source = Column(String(20), default=DataSource.MANUAL, nullable=False)
    # AI 解析的置信度 (0-1)
    confidence_score = Column(Float, nullable=True)
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        Index("ix_diet_log_user_date", "user_id", "log_date"),
        Index("ix_diet_log_user_date_meal", "user_id", "log_date", "meal_type"),
    )

    def to_dict(self, include_id=True):
        data = {
            "food_name": self.food_name,
            "weight_g": self.weight_g,
            "unit": self.unit,
            "calories": self.calories,
            "protein": self.protein,
            "fat": self.fat,
            "carbs": self.carbs,
            "source": self.source,
            "confidence_score": self.confidence_score,
            "notes": self.notes,
        }
        if include_id:
            data["id"] = str(self.id)
            data["log_id"] = str(self.log_id)
        return data


# ============================================================
# 表 3：用户饮食偏好（个性化学习结果）
# ============================================================

class UserFoodPreference(Base):
    """
    用户饮食偏好表。
    与 users 表一对一（user_id 唯一）。
    """
    __tablename__ = "user_food_preferences"

    id = Column(UUID(as_uuid=False), primary_key=True, default=generate_uuid)
    user_id = Column(
        UUID(as_uuid=False), ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False, unique=True, index=True,
    )
    # 饮食限制（如：乳糖不耐、蛋奶素）
    dietary_restrictions = Column(JSON, nullable=True)   # []
    # 过敏原（如：花生、虾）
    allergies = Column(JSON, nullable=True)              # []
    # 喜爱的菜系（如：川菜、日料）
    favorite_cuisines = Column(JSON, nullable=True)      # []
    # 不喜欢的食物
    avoided_foods = Column(JSON, nullable=True)          # []
    # 营养目标（每日）
    calorie_goal = Column(Integer, nullable=True)
    protein_goal = Column(Float, nullable=True)
    fat_goal = Column(Float, nullable=True)
    carbs_goal = Column(Float, nullable=True)
    # 系统学习到的平均摄入（用于智能推荐）
    avg_daily_calories = Column(Integer, nullable=True)
    common_foods = Column(JSON, nullable=True)           # 常吃食物
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def to_dict(self):
        return {
            "id": str(self.id),
            "user_id": str(self.user_id),
            "dietary_restrictions": self.dietary_restrictions or [],
            "allergies": self.allergies or [],
            "favorite_cuisines": self.favorite_cuisines or [],
            "avoided_foods": self.avoided_foods or [],
            "calorie_goal": self.calorie_goal,
            "protein_goal": self.protein_goal,
            "fat_goal": self.fat_goal,
            "carbs_goal": self.carbs_goal,
            "avg_daily_calories": self.avg_daily_calories,
            "common_foods": self.common_foods or [],
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }