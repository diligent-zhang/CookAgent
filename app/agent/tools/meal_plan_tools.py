"""
饮食规划相关 Tool：生成饮食计划、导出计划文件

每个 Tool 函数需要：
  1. 类型标注（LangChain 用它生成 tool schema）
  2. 文档字符串（LangChain 用它生成 tool description）
  3. 返回字符串（Agent 的 observation）
"""
import html as html_lib
import json
import os
import re
import uuid
from datetime import date, timedelta
from typing import Optional

from langchain_core.tools import tool

# 运行时注入的依赖
_rag_service = None
_llm_provider = None
_plan_storage_dir = None

# 导出文件允许的后缀 → 下载端点据此返回 media_type
EXPORT_MEDIA_TYPES = {
    ".md": "text/markdown; charset=utf-8",
    ".ics": "text/calendar; charset=utf-8",
    ".html": "text/html; charset=utf-8",
}

# 文件名白名单：meal_plan_YYYYMMDD_<8位hex>.<ext>（防路径穿越 + 防枚举）
PLAN_FILENAME_PATTERN = re.compile(
    r"^meal_plan_\d{8}_[0-9a-f]{8}\.(md|ics|html)$"
)


def is_safe_plan_filename(filename: str) -> bool:
    """校验下载文件名：严格匹配白名单格式，杜绝路径穿越与任意文件读取。"""
    return bool(PLAN_FILENAME_PATTERN.match(filename))


def get_plan_storage_dir() -> str:
    """返回导出文件的存储目录（绝对路径）。"""
    if _plan_storage_dir:
        return os.path.abspath(_plan_storage_dir)
    return os.path.abspath(
        os.path.join(
            os.path.dirname(__file__), "..", "..", "..", "data", "meal_plans"
        )
    )


def set_meal_plan_deps(rag_service, llm_provider=None, storage_dir=None):
    """运行时注入依赖（避免循环导入）。"""
    global _rag_service, _llm_provider, _plan_storage_dir
    _rag_service = rag_service
    _llm_provider = llm_provider
    if storage_dir:
        _plan_storage_dir = storage_dir
    else:
        _plan_storage_dir = os.path.join(
            os.path.dirname(__file__), "..", "..", "..", "data", "meal_plans"
        )


def _make_export_filename(ext: str) -> str:
    """生成导出文件名：meal_plan_YYYYMMDD_<uuid8>.<ext>。

    uuid 后缀避免同一天多次导出互相覆盖，也让 URL 不可猜测。
    """
    today_str = date.today().strftime("%Y%m%d")
    short_id = uuid.uuid4().hex[:8]
    return f"meal_plan_{today_str}_{short_id}.{ext}"


# ════════════════════════════════════════════════════════════════════
# plan_weekly_meals 使用的内部 prompt 模板
# ════════════════════════════════════════════════════════════════════

MEAL_PLAN_PROMPT = """你是一个专业的营养膳食规划师。请根据以下候选菜谱，为用户规划 {days} 天的饮食计划。

## 用户需求
- 天数：{days} 天
- 每日卡路里目标：{calories}
- 饮食限制：{restrictions}
- 菜系偏好：{preference}

## 可用候选菜谱
{recipes}

## 规划要求
1. 每天安排 **早餐、午餐、晚餐** 三餐，缺一不可
2. 荤素搭配，每天至少有一顿含优质蛋白（肉/蛋/鱼/豆制品）
3. 相邻两天的菜尽量不重复，保持饮食多样性
4. 严格遵守用户的饮食限制，绝不能安排过敏食材
5. 每餐标注预估卡路里（千卡）

## 输出格式（严格按以下 Markdown 格式）

# {days}天饮食计划

> 每日目标：{calories} | 饮食限制：{restrictions} | 偏好：{preference}

## 第 1 天（{start_date}）
- **早餐**：菜品名（约XXX千卡）
  简短说明（选这道的原因、营养亮点）
- **午餐**：菜品名（约XXX千卡）
  简短说明
- **晚餐**：菜品名（约XXX千卡）
  简短说明

## 第 2 天（{next_date}）
...

---

### 健康小贴士
（给 1-2 条实用的饮食建议）
"""


@tool
async def plan_weekly_meals(
    days: int = 7,
    calories_per_day: Optional[int] = None,
    dietary_restrictions: Optional[str] = None,
    cuisine_preference: Optional[str] = None,
) -> str:
    """
    制定饮食计划，规划每天早中晚三餐。生成一个结构化、可执行的每日三餐安排。

    适用场景：
    - 用户想制定一周减脂/增肌/保持体重的食谱
    - 用户想规划未来几天的每日三餐
    - 用户需要一份可打印、可导出的饮食计划

    参数：
        days: 规划天数，1-7 天，默认 7 天
        calories_per_day: 每日目标卡路里（可选），如 1500 表示每日约1500千卡
        dietary_restrictions: 饮食限制（可选），如"鸡蛋过敏"、"不吃辣"、"素食"
        cuisine_preference: 菜系偏好（可选），如"家常菜"、"川菜"、"清淡"
    """
    if _rag_service is None:
        return "错误：菜谱检索服务未初始化，请联系管理员。"
    if _llm_provider is None:
        return "错误：LLM 服务未初始化，请联系管理员。"

    days = max(1, min(days, 7))

    # ════════════════════════════════════════════════════════
    # Step 1: 为早中晚三餐分别搜索候选菜谱
    # ════════════════════════════════════════════════════════
    meal_queries = [
        ("早餐", "早餐 主食 粥 面 饼 蛋 豆浆 牛奶 轻食"),
        ("午餐", "午餐 荤素搭配 家常菜 下饭菜"),
        ("晚餐", "晚餐 清淡 易消化 汤 蔬菜"),
    ]

    if cuisine_preference:
        meal_queries = [
            (name, f"{query} {cuisine_preference}")
            for name, query in meal_queries
        ]
    if dietary_restrictions:
        meal_queries = [
            (name, f"{query} -{dietary_restrictions}")
            for name, query in meal_queries
        ]

    all_recipes = []
    seen_names = set()

    for meal_name, query in meal_queries:
        try:
            docs = await _rag_service.retrieve(query=query, top_k=6)
            for doc in docs:
                name = doc.metadata.get("dish_name", "未知菜品")
                if name in seen_names:
                    continue
                seen_names.add(name)
                all_recipes.append({
                    "name": name,
                    "meal_type": meal_name,
                    "content": doc.page_content[:400],
                    "category": doc.metadata.get("category", ""),
                    "difficulty": doc.metadata.get("difficulty", ""),
                })
        except Exception as e:
            return f"搜索{meal_name}菜谱时出错: {e}"

    # 如果候选菜谱不够（天数×3餐），补充一轮通用搜索
    if len(all_recipes) < days * 3:
        try:
            docs = await _rag_service.retrieve(query="家常菜 荤素 汤", top_k=10)
            for doc in docs:
                name = doc.metadata.get("dish_name", "未知菜品")
                if name not in seen_names:
                    seen_names.add(name)
                    all_recipes.append({
                        "name": name,
                        "meal_type": "通用",
                        "content": doc.page_content[:400],
                        "category": doc.metadata.get("category", ""),
                        "difficulty": doc.metadata.get("difficulty", ""),
                    })
        except Exception:
            pass  # 补充搜索失败不阻塞主流程

    if not all_recipes:
        return "未能检索到足够的菜谱数据。请尝试放宽搜索条件（如去掉菜系偏好或饮食限制）。"

    # ════════════════════════════════════════════════════════
    # Step 2: 格式化候选菜谱为 LLM 输入
    # ════════════════════════════════════════════════════════
    recipes_text_parts = []
    for r in all_recipes:
        recipes_text_parts.append(
            f"- [{r['meal_type']}] {r['name']} "
            f"(分类: {r['category']}, 难度: {r['difficulty']})\n"
            f"  摘要: {r['content'][:200]}"
        )
    recipes_text = "\n".join(recipes_text_parts)

    # ════════════════════════════════════════════════════════
    # Step 3: 调用 LLM 生成完整饮食计划
    # ════════════════════════════════════════════════════════
    today = date.today()
    cal_text = f"每日约 {calories_per_day} 千卡" if calories_per_day else "不限制"
    restrictions_text = dietary_restrictions or "无特殊限制"
    preference_text = cuisine_preference or "无特殊偏好"

    prompt = MEAL_PLAN_PROMPT.format(
        days=days,
        calories=cal_text,
        restrictions=restrictions_text,
        preference=preference_text,
        recipes=recipes_text,
        start_date=today.strftime("%Y-%m-%d"),
        next_date=(today + timedelta(days=1)).strftime("%Y-%m-%d"),
    )

    try:
        from langchain_core.messages import HumanMessage
        invoker = _llm_provider.create_invoker("normal", streaming=False)
        response = await invoker.ainvoke([HumanMessage(content=prompt)])
        plan_text = response.content if hasattr(response, "content") else str(response)
    except Exception as e:
        return f"调用 LLM 生成饮食计划时出错: {e}"

    return plan_text


@tool
async def export_meal_plan(plan_content: str) -> str:
    """
    将制定好的饮食计划导出为 Markdown 文件，保存到本地。
    调用后将返回文件路径，用户可以通过该路径下载或查看文件。

    适用场景：
    - 用户确认了饮食计划后，想保存为文件
    - 用户想打印或分享饮食计划

    参数：
        plan_content: 饮食计划的完整 Markdown 文本（即 plan_weekly_meals 的返回结果）
    """
    if _plan_storage_dir is None:
        return json.dumps({
            "status": "error",
            "message": "计划存储目录未初始化，请联系管理员。",
        }, ensure_ascii=False)

    try:
        os.makedirs(_plan_storage_dir, exist_ok=True)

        filename = _make_export_filename("md")
        filepath = os.path.join(_plan_storage_dir, filename)

        with open(filepath, "w", encoding="utf-8") as f:
            f.write(plan_content)

        download_url = f"/api/v1/agent/meal-plan/download/{filename}"
        return json.dumps({
            "status": "success",
            "filename": filename,
            "download_url": download_url,
            "message": f"饮食计划已导出，点击链接下载: {download_url}",
        }, ensure_ascii=False)
    except Exception as e:
        return json.dumps({
            "status": "error",
            "message": f"导出文件失败: {e}",
        }, ensure_ascii=False)


# ════════════════════════════════════════════════════════════════════
# 内部：从 Markdown 饮食计划中提取结构化数据
# ════════════════════════════════════════════════════════════════════

def _parse_plan_markdown(plan_text: str) -> list[dict]:
    """从 LLM 生成的 Markdown 计划中提取每天每餐的结构化数据。

    返回: [{"day": 1, "date": "2026-07-08", "meals": [
               {"type": "早餐", "dish": "小米粥", "calories": 150, "note": "..."},
               ...]}, ...]

    如果正则解析失败（提取不到足够数据），返回空列表，由调用方用 LLM 兜底。
    """
    days_data = []

    # 匹配每天的标题行：## 第 1 天（2026-07-08）或 ## 第 1 天(2026-07-08)
    day_pattern = re.compile(r"##\s*第\s*(\d+)\s*天[（(]([\d\-]+)[）)]")
    # 匹配每餐：- **早餐**：菜品名（约150千卡） 或 - **午餐**：菜品
    # 注意：菜名组必须用贪婪 [^（(\n]+（遇"（"或行尾自然停止）。
    # 若用懒惰 +?，后面的可选热量组会"匹配空"导致菜名被截成 1 个字符。
    meal_pattern = re.compile(
        r"-\s*\*\*([^*]+)\*\*[：:]\s*([^（(\n]+)(?:[（(]\s*约\s*(\d+)\s*千卡\s*[）)])?"
    )

    # 按"## 第"分割段落
    sections = re.split(r"\n(?=##\s*第\s*\d+\s*天)", plan_text)

    for section in sections:
        day_match = day_pattern.search(section)
        if not day_match:
            continue
        day_num = int(day_match.group(1))
        day_date = day_match.group(2)

        meals = []
        meal_matches = meal_pattern.finditer(section)
        for m in meal_matches:
            meal_type = m.group(1).strip()
            dish_name = m.group(2).strip()
            calories = int(m.group(3)) if m.group(3) else None
            meals.append({
                "type": meal_type,
                "dish": dish_name,
                "calories": calories,
            })

        if meals:
            days_data.append({
                "day": day_num,
                "date": day_date,
                "meals": meals,
            })

    return days_data


# ════════════════════════════════════════════════════════════════════
# export_meal_plan_ics — 导出为 ICS 日历文件
# ════════════════════════════════════════════════════════════════════

# 餐次对应的时间段
MEAL_TIMES = {
    "早餐": ("070000", "080000"),
    "午餐": ("120000", "130000"),
    "晚餐": ("180000", "190000"),
}

ICS_HEADER = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//CookHero//Meal Plan//ZH
CALSCALE:GREGORIAN
METHOD:PUBLISH
X-WR-CALNAME:CookHero 饮食计划
X-WR-CALDESC:由 CookHero AI 生成的每日三餐饮食计划
X-WR-TIMEZONE:Asia/Shanghai"""

ICS_FOOTER = "END:VCALENDAR"


def _ics_escape_text(value: str) -> str:
    """RFC 5545 TEXT 值转义：反斜杠、分号、逗号、换行。"""
    return (
        value.replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\r\n", "\\n")
        .replace("\n", "\\n")
    )


def _fold_ics_line(line: str) -> str:
    """RFC 5545 行折叠：单行超过 75 字节时用 CRLF+空格 折行。

    按 UTF-8 字节数计算，且不在多字节字符中间切断。
    """
    encoded = line.encode("utf-8")
    if len(encoded) <= 75:
        return line

    parts = []
    current = ""
    current_bytes = 0
    limit = 75
    for ch in line:
        ch_bytes = len(ch.encode("utf-8"))
        if current_bytes + ch_bytes > limit:
            parts.append(current)
            current = " " + ch  # 续行以空格开头
            current_bytes = 1 + ch_bytes
            limit = 74  # 预留前导空格后的宽度保持一致观感
        else:
            current += ch
            current_bytes += ch_bytes
    if current:
        parts.append(current)
    return "\r\n".join(parts)


def _plan_to_ics(days_data: list[dict]) -> str:
    """将结构化饮食数据转为 ICS 日历文本（CRLF 行结束 + 值转义 + 行折叠）。"""
    # 头部按行拆开，统一交给下方 CRLF join（避免三引号里的裸 \n 混入）
    raw_lines = [l for l in ICS_HEADER.split("\n") if l]
    emoji_map = {"早餐": "🍳", "午餐": "🍚", "晚餐": "🌙"}

    for day in days_data:
        day_date = day["date"]
        base_date = day_date.replace("-", "")

        for meal in day["meals"]:
            meal_type = meal["type"]
            emoji = emoji_map.get(meal_type, "🍽️")
            dish = meal["dish"]
            cal = f"{meal['calories']}千卡" if meal.get("calories") else ""

            # 找到对应的时间段
            meal_key = meal_type
            if meal_type not in MEAL_TIMES:
                # 模糊匹配："早"→早餐，"午"→午餐，"晚"→晚餐
                if "早" in meal_type:
                    meal_key = "早餐"
                elif "午" in meal_type or "中" in meal_type:
                    meal_key = "午餐"
                elif "晚" in meal_type:
                    meal_key = "晚餐"
                else:
                    meal_key = "午餐"  # 兜底

            start_time, end_time = MEAL_TIMES.get(meal_key, ("120000", "130000"))

            summary = f"{emoji} {meal_type} - {dish}"
            desc_parts = [f"{meal_type}: {dish}"]
            if cal:
                desc_parts.append(f"热量: {cal}")
                summary += f" ({cal})"

            raw_lines.extend([
                "BEGIN:VEVENT",
                f"UID:{uuid.uuid4()}",
                f"DTSTART;TZID=Asia/Shanghai:{base_date}T{start_time}",
                f"DTEND;TZID=Asia/Shanghai:{base_date}T{end_time}",
                f"SUMMARY:{_ics_escape_text(summary)}",
                f"DESCRIPTION:{_ics_escape_text(chr(10).join(desc_parts))}",
                "TRANSP:TRANSPARENT",
                "END:VEVENT",
            ])

    raw_lines.append(ICS_FOOTER)
    return "\r\n".join(_fold_ics_line(l) for l in raw_lines) + "\r\n"


@tool
async def export_meal_plan_ics(plan_content: str) -> str:
    """
    将饮食计划导出为 ICS 日历文件。ICS 文件可以导入到 Google Calendar、
    Apple Calendar（iPhone/Mac）、Outlook 等日历应用中，每餐自动显示在
    对应日期的时间段上（早餐 7:00、午餐 12:00、晚餐 18:00）。

    这是最推荐的导出格式——用户可以在手机日历上直接看到每天吃什么。

    适用场景：
    - 用户想把饮食计划导入手机/电脑日历
    - 用户需要每日用餐提醒
    - 用户想和家人共享饮食计划

    参数：
        plan_content: 饮食计划的完整 Markdown 文本（plan_weekly_meals 的返回结果）
    """
    if _plan_storage_dir is None:
        return json.dumps({"status": "error", "message": "存储目录未初始化"}, ensure_ascii=False)

    # 解析 markdown 提取结构化数据
    days_data = _parse_plan_markdown(plan_content)

    # 正则解析失败 → 用 LLM 兜底
    if not days_data:
        parse_prompt = f"""请从以下饮食计划 Markdown 中提取结构化数据，只返回 JSON，不要其他内容。

格式：
[{{"day": 1, "date": "2026-07-08", "meals": [{{"type": "早餐", "dish": "菜名", "calories": 150}}]}}]

计划内容：
{plan_content}"""

        try:
            from langchain_core.messages import HumanMessage
            invoker = _llm_provider.create_invoker("fast", streaming=False)
            response = await invoker.ainvoke([HumanMessage(content=parse_prompt)])
            raw = response.content if hasattr(response, "content") else str(response)
            # 清理 markdown 代码块包裹
            raw = raw.strip()
            if raw.startswith("```"):
                raw = re.sub(r"^```\w*\n?", "", raw)
                raw = re.sub(r"\n?```$", "", raw)
            days_data = json.loads(raw)
        except Exception:
            return json.dumps({
                "status": "error",
                "message": "无法解析饮食计划内容，请确认计划格式是否正确。",
            }, ensure_ascii=False)

    if not days_data:
        return json.dumps({"status": "error", "message": "计划内容为空，无法导出"}, ensure_ascii=False)

    # 生成 ICS
    try:
        ics_text = _plan_to_ics(days_data)
    except Exception as e:
        return json.dumps({"status": "error", "message": f"生成 ICS 失败: {e}"}, ensure_ascii=False)

    # 写文件
    try:
        os.makedirs(_plan_storage_dir, exist_ok=True)
        filename = _make_export_filename("ics")
        filepath = os.path.join(_plan_storage_dir, filename)

        with open(filepath, "w", encoding="utf-8") as f:
            f.write(ics_text)

        download_url = f"/api/v1/agent/meal-plan/download/{filename}"
        return json.dumps({
            "status": "success",
            "filename": filename,
            "download_url": download_url,
            "format": "ICS 日历",
            "message": (
                f"ICS 日历文件已生成！点击链接下载后，可直接导入到"
                f"iPhone/Android 日历、Google Calendar 或 Outlook 中，"
                f"每餐会自动显示在对应日期的时间段上。\n"
                f"下载: {download_url}"
            ),
        }, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"status": "error", "message": f"写入文件失败: {e}"}, ensure_ascii=False)


# ════════════════════════════════════════════════════════════════════
# export_meal_plan_html — 导出为精美 HTML 网页
# ════════════════════════════════════════════════════════════════════

HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{title}</title>
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC",
      "Microsoft YaHei", sans-serif;
    background: #f5f5f0;
    color: #333;
    padding: 24px;
  }}
  .container {{
    max-width: 900px;
    margin: 0 auto;
  }}
  .header {{
    text-align: center;
    padding: 32px 24px;
    background: linear-gradient(135deg, #ff6b35 0%, #f7931e 100%);
    border-radius: 16px;
    color: #fff;
    margin-bottom: 24px;
  }}
  .header h1 {{
    font-size: 28px;
    margin-bottom: 8px;
  }}
  .header .meta {{
    font-size: 14px;
    opacity: 0.9;
  }}
  .plan-table {{
    width: 100%;
    border-collapse: separate;
    border-spacing: 8px;
  }}
  .plan-table th {{
    padding: 12px 8px;
    text-align: center;
    font-size: 13px;
    color: #888;
    text-transform: uppercase;
    letter-spacing: 1px;
  }}
  .plan-table th.day-col {{
    background: #fff;
    border-radius: 12px;
    padding: 16px 8px;
    font-size: 14px;
    font-weight: 700;
    color: #333;
    box-shadow: 0 2px 8px rgba(0,0,0,0.06);
  }}
  .plan-table th.day-col .date {{
    display: block;
    font-weight: 400;
    font-size: 12px;
    color: #999;
    margin-top: 2px;
  }}
  .meal-card {{
    background: #fff;
    border-radius: 12px;
    padding: 16px;
    text-align: center;
    box-shadow: 0 2px 8px rgba(0,0,0,0.04);
    min-height: 100px;
    transition: transform 0.15s;
  }}
  .meal-card:hover {{
    transform: translateY(-2px);
    box-shadow: 0 4px 16px rgba(0,0,0,0.08);
  }}
  .meal-card .meal-label {{
    font-size: 11px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 1px;
    margin-bottom: 6px;
  }}
  .meal-card.breakfast .meal-label {{ color: #f7931e; }}
  .meal-card.lunch .meal-label {{ color: #27ae60; }}
  .meal-card.dinner .meal-label {{ color: #6c5ce7; }}
  .meal-card .dish-name {{
    font-size: 15px;
    font-weight: 600;
    color: #333;
    margin-bottom: 4px;
  }}
  .meal-card .calories {{
    display: inline-block;
    background: #f0f0f0;
    border-radius: 20px;
    padding: 2px 10px;
    font-size: 12px;
    color: #888;
  }}
  .meal-card.breakfast {{ border-top: 3px solid #f7931e; }}
  .meal-card.lunch {{ border-top: 3px solid #27ae60; }}
  .meal-card.dinner {{ border-top: 3px solid #6c5ce7; }}
  .tips {{
    margin-top: 24px;
    background: #fff;
    border-radius: 12px;
    padding: 20px 24px;
    box-shadow: 0 2px 8px rgba(0,0,0,0.04);
  }}
  .tips h3 {{
    font-size: 16px;
    margin-bottom: 8px;
    color: #ff6b35;
  }}
  .tips p {{ font-size: 14px; color: #666; line-height: 1.8; }}
  .footer {{
    text-align: center;
    margin-top: 24px;
    font-size: 12px;
    color: #bbb;
  }}
  @media print {{
    body {{ background: #fff; padding: 0; }}
    .meal-card {{ box-shadow: none; border: 1px solid #eee; }}
  }}
</style>
</head>
<body>
<div class="container">
  <div class="header">
    <h1>{title}</h1>
    <div class="meta">{meta_info}</div>
  </div>
  <table class="plan-table">
    <thead>
      <tr>
        <th></th>
        {day_headers}
      </tr>
    </thead>
    <tbody>
      {meal_rows}
    </tbody>
  </table>
  {tips_section}
  <div class="footer">由 CookHero AI 生成 · {generate_date}</div>
</div>
</body>
</html>"""


def _plan_to_html(plan_text: str) -> str:
    """将饮食计划渲染为精美 HTML 页面（所有动态内容做 HTML 转义）。"""
    esc = html_lib.escape

    # 提取标题
    title_match = re.search(r"^#\s*(.+)", plan_text, re.MULTILINE)
    title = esc(title_match.group(1).strip() if title_match else "饮食计划")

    # 提取元信息行 (> 每日目标: ...)
    meta_match = re.search(r"^>\s*(.+)", plan_text, re.MULTILINE)
    meta_info = esc(meta_match.group(1).strip() if meta_match else "")

    # 提取健康小贴士
    tips_match = re.search(r"###\s*健康小贴士\s*\n((?:.+\n?)+)", plan_text)
    tips_text = tips_match.group(1).strip() if tips_match else ""

    # 解析结构化数据
    days_data = _parse_plan_markdown(plan_text)

    # 构建表头：每天一列
    day_headers_parts = []
    for day in days_data:
        day_headers_parts.append(
            f'<th class="day-col">第{esc(str(day["day"]))}天'
            f'<span class="date">{esc(str(day["date"]))}</span></th>'
        )
    day_headers = "\n        ".join(day_headers_parts) if day_headers_parts else "<th>—</th>"

    # 构建行：早餐行、午餐行、晚餐行
    meal_types = ["早餐", "午餐", "晚餐"]
    css_classes = {"早餐": "breakfast", "午餐": "lunch", "晚餐": "dinner"}

    meal_rows_parts = []
    for meal_type in meal_types:
        cells = []
        for day in days_data:
            # 找到对应餐次
            found = None
            for meal in day["meals"]:
                if meal["type"] == meal_type:
                    found = meal
                    break
            if found:
                cal_badge = (
                    f'<span class="calories">{esc(str(found["calories"]))} 千卡</span>'
                    if found.get("calories") else ""
                )
                cells.append(
                    f'<td><div class="meal-card {css_classes[meal_type]}">'
                    f'<div class="meal-label">{meal_type}</div>'
                    f'<div class="dish-name">{esc(str(found["dish"]))}</div>'
                    f'{cal_badge}'
                    f'</div></td>'
                )
            else:
                cells.append(
                    f'<td><div class="meal-card {css_classes[meal_type]}">'
                    f'<div class="meal-label">{meal_type}</div>'
                    f'<div class="dish-name" style="color:#ccc">—</div>'
                    f'</div></td>'
                )
        meal_rows_parts.append(
            f'<tr>\n        <th>{meal_type}</th>\n        ' + "\n        ".join(cells) + "\n      </tr>"
        )
    meal_rows = "\n      ".join(meal_rows_parts)

    tips_section = ""
    if tips_text:
        # 先转义再保留换行（转义后的文本中换行可安全替换为 <br>）
        tips_html = esc(tips_text).replace("\n", "<br>")
        tips_section = f'<div class="tips"><h3>💡 健康小贴士</h3><p>{tips_html}</p></div>'

    return HTML_TEMPLATE.format(
        title=title,
        meta_info=meta_info,
        day_headers=day_headers,
        meal_rows=meal_rows,
        tips_section=tips_section,
        generate_date=date.today().strftime("%Y-%m-%d"),
    )


@tool
async def export_meal_plan_html(plan_content: str) -> str:
    """
    将饮食计划导出为精美的 HTML 网页文件。用浏览器打开后可以看到一个
    色彩分明的日历式表格：每天一列，每餐一张卡片，早餐橙色、午餐绿色、晚餐紫色。

    适合打印出来贴在冰箱上，或保存到手机桌面随时查看。

    适用场景：
    - 用户想要直观、美观的日历式饮食计划视图
    - 用户想打印饮食计划
    - 用户想保存为独立网页文件方便随时查看

    参数：
        plan_content: 饮食计划的完整 Markdown 文本（plan_weekly_meals 的返回结果）
    """
    if _plan_storage_dir is None:
        return json.dumps({"status": "error", "message": "存储目录未初始化"}, ensure_ascii=False)

    # 生成 HTML
    try:
        html_text = _plan_to_html(plan_content)
    except Exception as e:
        return json.dumps({"status": "error", "message": f"生成 HTML 失败: {e}"}, ensure_ascii=False)

    # 写文件
    try:
        os.makedirs(_plan_storage_dir, exist_ok=True)
        filename = _make_export_filename("html")
        filepath = os.path.join(_plan_storage_dir, filename)

        with open(filepath, "w", encoding="utf-8") as f:
            f.write(html_text)

        download_url = f"/api/v1/agent/meal-plan/download/{filename}"
        return json.dumps({
            "status": "success",
            "filename": filename,
            "download_url": download_url,
            "format": "HTML 网页",
            "message": (
                f"精美网页版饮食计划已生成！用浏览器打开即可看到一个"
                f"日历式表格：每天一列、每餐一张彩色卡片，"
                f"适合打印或保存到手机。\n"
                f"下载: {download_url}"
            ),
        }, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"status": "error", "message": f"写入文件失败: {e}"}, ensure_ascii=False)
