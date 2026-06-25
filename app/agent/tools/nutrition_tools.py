"""
营养相关 Tool：热量估算、食材替换

注意：这里的热量计算是粗略估算，不是精确的营养分析。
     真实场景应接入专业营养数据库（如 USDA FoodData Central）。
"""
from langchain_core.tools import tool

# 常见食材热量表（每 100g 的大卡数，粗略值）
CALORIE_MAP = {
    "鸡蛋": 144, "番茄": 18, "西红柿": 18, "土豆": 77, "猪肉": 395,
    "牛肉": 125, "鸡肉": 167, "鱼": 104, "虾": 99, "豆腐": 76,
    "白菜": 13, "青菜": 15, "菠菜": 23, "胡萝卜": 37, "黄瓜": 15,
    "茄子": 25, "青椒": 22, "洋葱": 40, "大蒜": 138, "姜": 80,
    "米饭": 116, "面条": 137, "面粉": 364, "油": 899, "盐": 0,
    "酱油": 53, "醋": 30, "糖": 400, "牛奶": 54, "奶酪": 350,
}

# 食材替换建议
SUBSTITUTE_MAP = {
    "鸡蛋": "亚麻籽粉（1:1 替代烘焙用）或豆腐（炒蛋替代）",
    "牛奶": "豆浆 / 燕麦奶 / 杏仁奶",
    "面粉": "杏仁粉 / 椰子粉 / 无麸质面粉",
    "猪肉": "鸡胸肉 / 牛肉（瘦肉）/ 豆腐（素食）",
    "糖": "蜂蜜 / 木糖醇 / 甜菊糖",
    "酱油": "无麸质酱油 / 椰子酱油 / 酱油膏",
    "花生油": "橄榄油 / 菜籽油 / 椰子油",
    "虾": "鱼肉块 / 豆腐 / 猴头菇（素食口感）",
}


@tool
def estimate_calories(ingredients: str) -> str:
    """
    估算一道菜或一份食材的大致热量。

    适用于：
    - 用户在控制饮食，想知道某道菜的热量
    - 用户想比较不同做法的热量差异

    注意：这是粗略估算，仅供参考，不构成专业的营养建议。
    如需精确数据，建议使用专业营养分析工具。

    参数：
        ingredients: 食材及其用量，格式如"鸡蛋2个,番茄1个,油10克"
    """
    if not ingredients.strip():
        return "请提供食材信息，例如：鸡蛋2个,番茄1个"

    parts = []
    total_calories = 0
    not_found = []

    for item in ingredients.split(","):
        item = item.strip()
        if not item:
            continue

        # 简单解析："鸡蛋2个" → ingredient="鸡蛋", amount_str="2个"
        matched = False
        for name, cal_per_100g in CALORIE_MAP.items():
            if name in item:
                # 粗略估算：不精确解析用量，按每份估算
                # "1个鸡蛋" ≈ 50g, "1个番茄" ≈ 150g
                if "个" in item:
                    try:
                        count = int(item.replace(name, "").replace("个", "").strip() or 1)
                    except ValueError:
                        count = 1
                    grams = count * 50  # 粗略假设每个 50g
                elif "克" in item or "g" in item or "G" in item:
                    try:
                        grams = int(''.join(c for c in item if c.isdigit()) or 100)
                    except ValueError:
                        grams = 100
                else:
                    grams = 100  # 默认 100g

                calories = cal_per_100g * grams / 100
                total_calories += calories
                parts.append(
                    f"- {item} → 约 {grams}g × {cal_per_100g}kcal/100g ≈ {calories:.0f}kcal"
                )
                matched = True
                break

        if not matched:
            not_found.append(item)

    result = "\n".join(parts)
    result += f"\n\n总计约 {total_calories:.0f} kcal"

    if not_found:
        result += f"\n\n未找到以下食材的热量数据：{'、'.join(not_found)}"

    return result


@tool
def suggest_substitutes(ingredient: str) -> str:
    """
    为某样食材推荐替代方案。适用于用户对某样食材过敏、买不到、或想换口味。

    参数：
        ingredient: 要替换的食材名称，如"鸡蛋"
    """
    if ingredient in SUBSTITUTE_MAP:
        return (
            f"「{ingredient}」的替代建议：\n"
            f"{SUBSTITUTE_MAP[ingredient]}"
        )

    # 模糊匹配
    for key, value in SUBSTITUTE_MAP.items():
        if ingredient in key or key in ingredient:
            return (
                f"「{ingredient}」的替代建议（基于「{key}」）：\n"
                f"{value}"
            )

    return (
        f"暂时没有「{ingredient}」的直接替代数据。\n"
        f"一般原则：寻找口感、烹饪方式相似的食材。\n"
        f"可以尝试在菜谱检索中搜索不含该食材的类似菜品。"
    )
