# CookAgent P2 设计方案

> 日期: 2026-06-26 | 参考项目: CookHero | 状态: 设计阶段

---

## 一、P2 目标

P1 完成了"菜谱专家"——用户说想吃啥，Agent 能搜到菜谱。P2 从"烹饪助手"拓展到"饮食管理平台"：

- 用户能制定一周饮食计划
- 能用自然语言记录吃了什么（AI 自动解析热量）
- 能拍照片识别食物
- Agent 能根据天气、季节推荐菜谱
- 用户能评价回答质量，系统自动改进

```
P1 (当前)                          P2 (目标)
  用户消息                            用户消息
    │                                   │
    ▼                                   ▼
  IntentDetector                     IntentDetector
    │                                   │
    ▼                                   ▼
  AgentRegistry                      AgentRegistry
    │                                   │
    ├── recipe_search                  ├── recipe_search
    ├── cooking_help                   ├── cooking_help
    ├── general_chat                   ├── general_chat
    └── diet_plan (骨架)               ├── diet_plan ★ 完整实现
                                       ├── diet_log   ★ 新增
                                       ├── diet_analysis ★ 新增
                                       └── ingredient_ask
                                            │
                                    工具系统升级:
                                    ├── 6个已有工具 (P1)
                                    ├── 3个饮食工具 (P2 新增)
                                    ├── 天气工具 (P2 新增)
                                    ├── 时令食材工具 (P2 新增)
                                    ├── MCP 外部服务 (P2 新增)
                                    └── 子代理系统 (P2 新增)
```

---

## 二、整体架构升级

### 2.1 新增模块目录

```
app/
├── diet/                          ← P2 新增：饮食管理模块
│   ├── __init__.py
│   ├── models.py                  ← DietPlan, DietLogItem, FoodPreference
│   ├── repository.py              ← 饮食数据 CRUD
│   ├── service.py                 ← DietService 核心业务
│   ├── prompts/
│   │   └── log_parsing.py         ← AI 饮食日志解析提示词
│   └── tools/
│       ├── __init__.py            ← 注册饮食工具
│       ├── diet_plan_tool.py      ← 饮食计划工具
│       ├── diet_log_tool.py       ← 饮食记录工具
│       └── diet_analysis_tool.py  ← 营养分析工具
│
├── agent/
│   ├── tools/
│   │   ├── weather_tool.py        ← P2 新增：天气查询工具
│   │   ├── seasonal_food_tool.py  ← P2 新增：时令食材工具
│   │   └── base.py                ← P2 新增：BaseTool 抽象类
│   ├── context.py                 ← P2 新增：AgentContextBuilder
│   ├── subagents/
│   │   ├── base.py                ← P2 新增：BaseSubagent
│   │   ├── tool.py                ← P2 新增：SubagentTool 包装器
│   │   └── builtin/
│   │       └── diet_planner.py    ← P2 升级：完整饮食规划子代理
│   └── registry/
│       └── hub.py                 ← P2 升级：AgentHub 统一工具管理
│
├── vision/                        ← P2 新增：多模态模块
│   ├── provider.py                ← VisionProvider 图片分析
│   └── agent.py                   ← VisionAgent 食物识别
│
├── evaluation/                    ← P2 新增：评价系统
│   ├── models.py                  ← EvaluationModel
│   ├── service.py                 ← RAGAS 评估服务
│   └── router.py                  ← /evaluations API
│
├── mcp/                           ← P2 新增：MCP 客户端
│   ├── client.py                  ← JSON-RPC MCP 客户端
│   └── setup.py                   ← MCP 服务器注册
│
├── config/
│   └── config.py                  ← P2 升级：新增配置项
│
└── api/v1/endpoints/
    ├── diet.py                    ← P2 新增：/diet/* API
    └── evaluation.py              ← P2 新增：/evaluations API
```

---

## 三、饮食管理模块 (Diet Module)

### 3.1 数据模型

```python
# app/diet/models.py

class MealType(str, enum.Enum):
    BREAKFAST = "breakfast"
    LUNCH = "lunch"
    DINNER = "dinner"
    SNACK = "snack"

class DataSource(str, enum.Enum):
    MANUAL = "manual"        # 用户手动录入
    AI_TEXT = "ai_text"      # AI 自然语言解析
    AI_IMAGE = "ai_image"    # AI 图片识别

# 饮食计划：一顿饭的计划
class DietPlanMeal(Base):
    __tablename__ = "diet_plan_meals"
    id            UUID      PK
    user_id       UUID      FK → users
    plan_date     Date
    meal_type     MealType  (早/午/晚/加餐)
    dishes        JSONB     [{name, calories, protein, fat, carbs, quantity}]
    total_calories  Integer
    total_protein   Float
    total_fat       Float
    total_carbs     Float
    notes         Text
    created_at    DateTime
    updated_at    DateTime

    # 索引: (user_id, plan_date) 和 (user_id, plan_date, meal_type)

# 饮食记录：用户实际吃的一口食物
class DietLogItem(Base):
    __tablename__ = "diet_log_items"
    id            UUID      PK
    log_id        UUID      (同一次记录的多条食物共享)
    user_id       UUID      FK → users
    log_date      Date
    meal_type     MealType
    plan_meal_id  UUID?     FK → diet_plan_meals (如果能关联到计划)
    food_name     String
    weight_g      Integer   (估算克数)
    unit          String    (份/碗/个)
    calories      Integer
    protein       Float
    fat           Float
    carbs         Float
    source        DataSource
    confidence    Float     (AI 解析的置信度 0-1)
    notes         Text
    created_at    DateTime

    # 索引: (user_id, log_date) 和 (user_id, log_date, meal_type)

# 用户饮食偏好（AI 学习的数据）
class UserFoodPreference(Base):
    __tablename__ = "user_food_preferences"
    id                  UUID   PK
    user_id             UUID   FK → users, UNIQUE
    dietary_restrictions List   (如: ["蛋奶素", "不吃香菜"])
    allergies           List   (如: ["鸡蛋", "花生"])
    favorite_cuisines   List   (如: ["川菜", "日料"])
    avoided_foods       List   (如: ["苦瓜", "芹菜"])
    calorie_goal        Integer (每日热量目标)
    protein_goal        Float
    fat_goal            Float
    carbs_goal          Float
    avg_daily_calories  Integer (系统统计的学习值)
    created_at          DateTime
    updated_at          DateTime
```

### 3.2 核心业务逻辑（伪代码）

```python
# app/diet/service.py

class DietService:
    """饮食管理核心服务。同时被 REST API 和 Agent 工具使用。"""

    # ========== 计划管理 ==========

    async def get_plan_by_week(user_id, week_start_date) -> list[DietPlanMeal]
        """获取用户某周的饮食计划，按天+餐次分组。"""

    async def add_meal(user_id, plan_date, meal_type, dishes) -> DietPlanMeal
        """
        添加一顿计划餐。
        dishes = [
            {name:"清蒸鲈鱼", calories:280, protein:35, fat:12, carbs:5, quantity:"1条"},
            {name:"蒜蓉西兰花", calories:80, protein:5, fat:3, carbs:10, quantity:"1份"},
        ]
        自动汇总 total_calories/protein/fat/carbs。
        """

    async def update_meal(meal_id, **updates)
    async def delete_meal(meal_id)
    async def copy_meal(source_meal_id, target_date, target_meal_type)
        """把某餐复制到另一天/另一餐次，方便复用。"""

    # ========== 记录管理 ==========

    async def log_manual(user_id, log_date, meal_type, items) -> list[DietLogItem]
        """
        手动记录饮食。
        items = [{food_name:"米饭", weight_g:200, calories:230}, ...]
        一个 log_id 关联多条 item。
        """

    async def log_from_text(user_id, text, image_url=None) -> list[DietLogItem]
        """
        AI 解析自然语言/图片记录饮食。★ 核心功能。

        流程：
          1. 如果有 image_url → VisionProvider.analyze(image) → 识别食物
          2. fast LLM 解析 text + (图片识别结果) → 结构化 JSON
             prompt = "解析这条饮食记录：{text}。图片显示：{vision_result}"
             LLM 返回: {
               meal_type: "lunch",
               items: [
                 {food_name:"红烧肉", weight_g:150, calories:350},
                 {food_name:"米饭", weight_g:200, calories:230},
               ]
             }
          3. 解析失败 → 降级为 manual 模式：把原文本当 food_name 存
          4. 写入数据库，tag source="ai_text" 或 "ai_image"
        """

    async def mark_as_eaten(user_id, plan_meal_id) -> list[DietLogItem]
        """把计划中的一餐标记为已吃，自动转换。"""

    # ========== 营养分析 ==========

    async def get_daily_summary(user_id, date) -> dict
        """某天的营养汇总。返回 {calories, protein, fat, carbs, meals_logged, log_count}"""

    async def get_weekly_summary(user_id, week_start) -> dict
        """一周汇总 + 每日趋势。返回 {daily_data:[...], weekly_total, average}"""

    async def get_deviation_analysis(user_id, week_start) -> dict
        """
        计划 vs 实际偏差分析。
        返回 {execution_rate, total_deviation_pct, per_meal_details}
        """

    async def get_user_preference(user_id) -> UserFoodPreference
    async def update_user_preference(user_id, **kwargs)
```

---

## 四、工具系统升级

### 4.1 当前问题

P1 的工具系统用 LangChain 的 `@tool` 装饰器，所有工具放在 `get_all_tools()` 里硬编码返回。问题：

- 工具的返回格式不统一（有些返回字符串，有些返回 dict）
- 无法动态增删工具（比如用户不想用热量估算工具）
- 新增工具要改 `get_all_tools()` 和 `__init__.py`

### 4.2 升级方案：BaseTool + ToolExecutor + AgentHub

参考 CookHero 的三层架构，在保留现有 LangChain `@tool` 兼容的同时加一层抽象：

```
                        AgentHub (全局单例)
                      注册/查找/列表/创建执行器
                             │
              ┌──────────────┼──────────────┐
              ▼              ▼              ▼
     LocalToolProvider  MCPToolProvider  SubagentToolProvider
     (Python 工具)      (远程 MCP 服务)  (子代理包装)
              │              │              │
              ▼              ▼              ▼
     search_recipes    mcp_amap_*     subagent_diet_planner
     diet_plan         mcp_weather_*  subagent_xxx
     weather           ...
```

```python
# app/agent/tools/base.py

from abc import ABC, abstractmethod
from pydantic import BaseModel

class ToolResult(BaseModel):
    """统一的工具返回格式。替代 P1 的纯字符串返回。"""
    success: bool
    data: Any = None        # 结构化数据
    error: str | None = None

class BaseTool(ABC):
    """所有工具的基类。P1 的 @tool 装饰器函数逐步迁移到此接口。"""
    name: str = ""
    description: str = ""
    parameters: dict = {}   # JSON Schema

    @abstractmethod
    async def execute(self, **kwargs) -> ToolResult:
        ...

    def to_openai_schema(self) -> dict:
        """转为 OpenAI function calling 格式。"""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            }
        }

# app/agent/registry/hub.py (P2 升级版)

class AgentHub:
    """
    统一工具管理中心（P2 升级 P1 的 AgentRegistry）。
    P1 的 AgentRegistry 功能保留：Agent 注册 + 意图路由。
    P2 新增：工具注册 + 多 Provider + 动态工具列表 + ToolExecutor 创建。
    """

    _providers: list[ToolProvider] = []   # 按优先级排列
    _tool_cache: dict[str, BaseTool] = {} # 工具缓存

    @classmethod
    def register_provider(cls, provider: ToolProvider):
        """注册工具提供者。启动时按 local → mcp → subagent 顺序注册。"""

    @classmethod
    def get_tool(cls, name: str, user_id: str = None) -> BaseTool:
        """按名称查找工具。遍历所有 provider，返回第一个匹配。"""

    @classmethod
    def list_tools(cls, user_id: str = None) -> list[str]:
        """列出当前可用的所有工具名称。Subagent 工具按 user 过滤。"""

    @classmethod
    def create_tool_executor(cls, tool_names: list[str], user_id: str = None) -> ToolExecutor:
        """创建工具执行器，只包含指定的工具子集。"""
```

### 4.3 新增 P2 工具

```python
# app/diet/tools/diet_plan_tool.py

class DietPlanTool(BaseTool):
    """
    饮食计划工具。Agent 用它为用户制定/修改/查看一周饮食计划。

    actions:
      - add_meal:      添加一顿计划餐
      - update_meal:    修改某餐
      - delete_meal:    删除某餐
      - copy_meal:      把某餐复制到另一天
      - get_by_week:    查看某周的计划

    设计决策：不用拆成 5 个独立工具，用一个 tool + action 参数。
    好处：Agent 只需记住一个工具名，减少 function calling 的决策空间。
    LLM 通过 action 参数选择具体操作（类似 REST API 的一个 endpoint）。
    """
    name = "diet_plan"
    description = "管理饮食计划。可添加、修改、删除、查看某周的饮食安排。"
    parameters = {
        "type": "object",
        "properties": {
            "action": {"type":"string", "enum":["add_meal","update_meal","delete_meal","copy_meal","get_by_week"]},
            "week_start_date": {"type":"string", "description":"周一日期 YYYY-MM-DD"},
            "plan_date": {"type":"string", "description":"具体日期 YYYY-MM-DD"},
            "meal_type": {"type":"string", "enum":["breakfast","lunch","dinner","snack"]},
            "dishes": {"type":"array", "items":{"type":"object"}},
            "notes": {"type":"string"},
        },
        "required": ["action"]
    }

    async def execute(self, **kwargs) -> ToolResult:
        action = kwargs["action"]
        user_id = kwargs["user_id"]

        match action:
            case "add_meal":
                meal = await self.diet_service.add_meal(
                    user_id=user_id,
                    plan_date=kwargs["plan_date"],
                    meal_type=kwargs["meal_type"],
                    dishes=kwargs["dishes"],
                )
                return ToolResult(success=True, data={"meal": meal.to_dict()})
            case "get_by_week":
                meals = await self.diet_service.get_plan_by_week(
                    user_id=user_id,
                    week_start_date=kwargs["week_start_date"],
                )
                return ToolResult(success=True, data={"meals": [m.to_dict() for m in meals]})
            # ... 其他 action 同理


# app/diet/tools/diet_log_tool.py

class DietLogTool(BaseTool):
    """
    饮食记录工具。Agent 用它帮用户记录每天吃了什么。

    亮点是 log_from_text action：
      用户对 Agent 说"中午吃了半碗米饭配红烧肉"，
      Agent 调此 action → 后台 LLM 解析 → 结构化存储。
      用户不需要手动填热量、克数。
    """
    name = "diet_log"
    actions = ["log", "log_from_text", "get_by_date", "delete", "mark_eaten"]


# app/diet/tools/diet_analysis_tool.py

class DietAnalysisTool(BaseTool):
    """
    营养分析工具。Agent 用它生成日报/周报/偏差分析。

    daily_summary  → 今天的营养摄入汇总
    weekly_summary → 这周的摄入趋势
    deviation      → 计划 vs 实际对比
    preferences    → 查看/更新饮食偏好
    """
    name = "diet_analysis"


# app/agent/tools/weather_tool.py

class WeatherTool(BaseTool):
    """
    天气查询工具。Agent 用它获取指定城市的天气。

    使用高德天气 API（免费，国内稳定）。

    为什么 Agent 需要天气？
      - "今天35度，推荐清凉消暑的菜"
      - "明天下雨，适合在家里煲汤"
      - "这周降温10度，推荐暖身滋补的菜"
    """
    name = "weather"
    description = "查询指定城市的实时天气和未来3天预报。"

    async def execute(self, **kwargs) -> ToolResult:
        city = kwargs.get("city", "北京")
        # 调用高德天气 API
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                "https://restapi.amap.com/v3/weather/weatherInfo",
                params={"city": city, "key": settings.amap_api_key}
            )
            data = resp.json()
        return ToolResult(success=True, data={
            "city": city,
            "temperature": data["lives"][0]["temperature"],
            "weather": data["lives"][0]["weather"],
            "humidity": data["lives"][0]["humidity"],
            "forecast": data.get("forecasts", []),  # 3天预报
        })


# app/agent/tools/seasonal_food_tool.py

class SeasonalFoodTool(BaseTool):
    """
    时令食材工具。Agent 用它推荐当季应季食材。

    为什么不用 API？
      时令食材数据变化很慢（每年一样），本地维护一个知识库就够了，
      不需要花钱调 API。每年更新一次即可。

    为什么 Agent 需要时令食材？
      - "春天有什么时令菜" → 推荐春笋、韭菜、荠菜
      - "夏天推荐消暑的菜" → 结合时令食材 + 菜谱库
    """
    name = "seasonal_foods"
    description = "获取当前季节的时令食材和推荐做法。不传 season 则自动判断。"

    # 本地维护的时令知识库（每年更新一次）
    SEASONAL_DATA = {
        "春": {
            "months": [3, 4, 5],
            "vegetables": ["春笋","韭菜","菠菜","荠菜","香椿","豌豆苗"],
            "fruits": ["草莓","樱桃","枇杷","桑葚"],
            "cooking_tips": ["清炒","凉拌","蒸","快炒（保留鲜嫩口感）"],
        },
        "夏": {
            "months": [6, 7, 8],
            "vegetables": ["苦瓜","冬瓜","丝瓜","黄瓜","番茄","空心菜"],
            "fruits": ["西瓜","桃子","荔枝","杨梅","葡萄"],
            "cooking_tips": ["凉拌","清蒸","白灼","少油快炒"],
        },
        "秋": { ... },
        "冬": { ... },
    }

    async def execute(self, **kwargs) -> ToolResult:
        month = datetime.now().month
        season = next(s for s, data in SEASONAL_DATA.items()
                      if month in data["months"])
        return ToolResult(success=True, data={
            "season": season,
            **SEASONAL_DATA[season],
        })
```

---

## 五、子代理系统 (SubAgent Pattern)

### 5.1 设计动机

P1 的 Agent 是平级的：RecipeMasterAgent、GeneralAgent、DietPlannerAgent 各自独立。问题是：

- 每个 Agent 都要完整实现 ReAct 循环 → 代码重复
- 复杂任务（如"帮我制定一周减脂食谱"）需要多步推理 → 主 Agent 的 ReAct 循环会很长
- 子任务（如"查天气 → 选时令食材 → 搜菜谱 → 排计划"）应该是独立的工作流

### 5.2 方案：主 Agent + 子代理工具

```
用户: "帮我制定一周减脂食谱，我在北京，最近天气热"

主 Agent (ReAct 循环, max_iterations=10)
  │
  ├─ [tool_call: weather]  → "北京 35°C 晴"
  ├─ [tool_call: seasonal_foods] → "夏季时令：冬瓜、苦瓜、丝瓜..."
  │
  ├─ [tool_call: subagent_diet_planner]  ★ 把整个规划任务委托给子代理
  │     │
  │     │  子代理内部 ReAct 循环 (max_iterations=15):
  │     │    1. 获取用户饮食偏好 → 减脂、1500卡/天、不吃牛肉
  │     │    2. 搜索减脂 + 夏季 + 时令食材的菜谱
  │     │    3. 分析每个菜谱的营养数据
  │     │    4. 编排 7 天 × 3 餐的计划
  │     │    5. 检查热量总和是否在目标范围内
  │     │    6. 返回完整的 Markdown 计划
  │     │
  │     └→ ToolResult(success=True, data={plan_markdown: "..."})
  │
  └→ 主 Agent 把子代理的结果展示给用户
```

**核心思想：主 Agent 做编排，子代理做执行。** 主 Agent 只决定"该调用哪个子代理"和"什么时候调用"，子代理内部有自己独立的 system prompt + 工具集 + ReAct 循环。

### 5.3 实现（伪代码）

```python
# app/agent/subagents/base.py

class BaseSubagent(ABC):
    """
    子代理基类。本质是一个"有自己 ReAct 循环的 Agent"，
    但对外暴露为一个工具。

    与 BaseAgent 的区别：
      BaseAgent 是用户直接面对的 Agent（处理完整对话）。
      BaseSubagent 是主 Agent 调用的工具（处理专项任务）。
    """
    name: str
    description: str
    system_prompt: str
    tools: list[str]           # 工具名列表（从 AgentHub 查找）
    max_iterations: int = 10

    @abstractmethod
    async def execute(
        self,
        task: str,              # 主 Agent 给的任务描述
        user_id: str,
        background: str = None,  # 主 Agent 提供的背景信息
        event_handler: Callable = None,  # 用于流式推送子代理的中间步骤
    ) -> ToolResult:
        ...

    async def run_with_tools(self, task, user_id, background=None, event_handler=None):
        """
        子代理内部的 ReAct 循环。

        与 ReActAgent.run() 的区别：
          - 子代理不直接面向用户，中间过程通过 event_handler 推送
          - 子代理的工具集是主 Agent 工具集的子集（安全隔离）
          - 子代理有独立的 system prompt（专业领域知识）
        """
        messages = [
            SystemMessage(self.system_prompt),
            SystemMessage(f"用户背景：{background}") if background else None,
            HumanMessage(task),
        ]

        tool_executor = AgentHub.create_tool_executor(self.tools, user_id)

        for iteration in range(self.max_iterations):
            response = await self.llm.ainvoke_with_tools(messages, tool_executor.schemas())

            if response.tool_calls:
                for tc in response.tool_calls:
                    if event_handler:
                        await event_handler({"type":"tool_call","tool":tc.name,"args":tc.args})
                    result = await tool_executor.execute(tc.name, tc.args)
                    messages.append(ToolMessage(content=str(result.data)))
                    if event_handler:
                        await event_handler({"type":"tool_result","result":result})
            else:
                return ToolResult(success=True, data={"result": response.content})

        return ToolResult(success=False, error="超过最大迭代次数")


# app/agent/subagents/tool.py

class SubagentTool(BaseTool):
    """
    把子代理包装为普通工具。
    主 Agent 看到它和其他工具（search_recipes、weather）没有区别。
    """
    def __init__(self, subagent: BaseSubagent):
        self.subagent = subagent
        self.name = f"subagent_{subagent.name}"   # 命名前缀 subagent_
        self.description = subagent.description
        self.parameters = {
            "type": "object",
            "properties": {
                "task": {"type":"string", "description":"委托给子代理的具体任务"},
                "background": {"type":"string", "description":"用户背景信息"},
            },
            "required": ["task"]
        }

    async def execute(self, **kwargs) -> ToolResult:
        return await self.subagent.execute(
            task=kwargs["task"],
            user_id=kwargs.get("user_id"),
            background=kwargs.get("background"),
            event_handler=kwargs.get("event_handler"),
        )


# app/agent/subagents/builtin/diet_planner.py

class DietPlannerSubagent(BaseSubagent):
    """
    饮食规划子代理（P2 完整版，替换 P1 的骨架）。

    可用工具: datetime, weather, seasonal_foods, search_recipes,
              diet_analysis, diet_plan
    """
    name = "diet_planner"
    description = "饮食规划专家：根据用户目标、时令、天气、偏好制定一周食谱"
    system_prompt = """你是饮食规划专家。根据用户的目标(减脂/增肌/均衡)、
    当前季节时令食材、天气情况、饮食偏好和过敏限制，为用户制定科学合理的饮食计划。
    计划以 Markdown 格式输出，包含每日三餐+加餐、热量汇总、采购清单。"""
    tools = ["datetime", "weather", "seasonal_foods", "search_recipes",
             "diet_analysis", "diet_plan"]
    max_iterations = 15

    async def execute(self, task, user_id, background=None, event_handler=None):
        # 补充用户偏好到 background
        pref = await self.diet_service.get_user_preference(user_id)
        enriched_bg = f"{background}\n用户偏好：{pref.to_dict()}" if background else f"用户偏好：{pref.to_dict()}"
        return await self.run_with_tools(task, user_id, enriched_bg, event_handler)
```

---

## 六、AgentContextBuilder（上下文构建器）

### 6.1 当前问题

P1 的 AgentContext 构建分散在 `AgentService.stream_agent_chat()` 里——手搓 AgentContext dataclass，字段不全，没有用户画像注入。

### 6.2 方案

```python
# app/agent/context.py

class AgentContextBuilder:
    """
    逐步构建 Agent 执行所需的完整上下文。

    替代 P1 中在 AgentService 里手搓 AgentContext 的做法。

    构建步骤：
      1. system_prompt   → 从 Agent 配置中取
      2. user_profile    → 从 UserService 取用户画像和指令
      3. history         → 会话历史摘要 + 最近消息
      4. tools           → 从 AgentHub 取可用工具列表
      5. vision          → 如果有图片，预处理视觉分析
      6. build()         → 返回完整的 AgentContext
    """

    def __init__(self, session, user_id, agent_name, db, llm_provider):
        self._session = session
        self._user_id = user_id
        self._agent_name = agent_name
        ...

    async def with_user_profile(self):
        """注入用户画像和饮食偏好。"""
        user = await self._user_service.get_user_by_id(self._user_id)
        diet_pref = await self._diet_service.get_user_preference(self._user_id)
        self._user_profile = {
            "bio": user.profile,
            "instruction": user.user_instruction,
            "allergies": diet_pref.allergies,
            "calorie_goal": diet_pref.calorie_goal,
        }
        return self  # 链式调用

    async def with_tools(self, selected_tools=None):
        """注入可用工具列表。如果传了 selected_tools，只取子集。"""
        if selected_tools:
            self._tool_schemas = AgentHub.get_tool_schemas(selected_tools, self._user_id)
        else:
            self._tool_schemas = AgentHub.get_tool_schemas(
                AgentHub.list_tools(self._user_id), self._user_id
            )
        return self

    async def with_history(self):
        """注入会话历史（压缩摘要 + 最近消息）。"""
        self._history_summary = await self._repo.get_compressed_summary(self._session.id)
        self._recent_messages = await self._repo.get_recent_messages(self._session.id, limit=20)
        return self

    async def with_vision(self, images):
        """如果有上传图片，预处理视觉分析。"""
        if images:
            self._vision_result = await self._vision_agent.analyze(images, ...)
        return self

    async def build(self) -> AgentContext:
        """组装最终上下文。P2 版 AgentContext 比 P1 多了 user_profile 和 vision 字段。"""
        return AgentContext(
            user_id=self._user_id,
            session_id=self._session.id,
            system_prompt=...,
            user_profile=self._user_profile,
            history_summary=self._history_summary,
            recent_messages=self._recent_messages,
            available_tools=self._tool_schemas,
            vision_analysis=self._vision_result,
            ...
        )
```

---

## 七、多模态模块 (Vision Module)

### 7.1 使用场景

- 用户拍冰箱里的食材 → Agent 推荐能做什么菜
- 用户拍做好的菜 → Agent 估算热量
- 用户拍餐厅菜单 → Agent 分析营养
- 饮食记录时拍照 → AI 自动识别食物并记录

### 7.2 实现（伪代码）

```python
# app/vision/provider.py

class VisionProvider:
    """
    视觉分析提供者。封装多模态 LLM 调用。

    使用 qwen-vl-max（通义千问视觉模型），支持中英文图片理解。
    """

    async def analyze(self, text: str, images: list[ImageInput],
                       system_prompt: str = None) -> str:
        """
        分析图片，返回文字描述。

        构建多模态消息：
          HumanMessage([
            {"type":"text", "text":"这张图片里有什么食物？"},
            {"type":"image_url", "image_url":{"url":"https://imgbb.com/xxx.jpg"}},
          ])
        调用 vision LLM 获取结果。
        """

    def validate_image(self, mime_type: str, size_bytes: int) -> tuple[bool, str]:
        """验证图片格式和大小。限制 10MB，支持 jpg/png/webp。"""


# app/vision/agent.py

class VisionAgent:
    """
    高层视觉分析——从图片中提取食物相关信息。

    与 VisionProvider 的区别：
      Provider 是底层 LLM 调用封装。
      Agent 是高层的意图理解 + 结构化输出。
    """

    async def analyze(self, images, user_query=None,
                       user_context=None) -> VisionResult:
        """
        分析食物图片，返回结构化结果。

        例如拍了一盘菜：
          VisionResult(
            is_food=True,
            dish_name="番茄炒蛋",
            ingredients=["番茄","鸡蛋","葱"],
            estimated_calories=280,
            cooking_method="炒",
          )

        如果拍的是一堆食材：
          VisionResult(
            is_food=True,
            dish_name=None,
            ingredients=["鸡胸肉","青椒","土豆","胡萝卜"],
            suggested_recipes=["青椒鸡丁","土豆烧鸡块","宫保鸡丁"],
          )
        """
```

### 7.3 与 Agent 的集成

```
AgentService.stream_agent_chat() 的 Step 4.5（新增）：

  if context.images:
      vision_result = await vision_agent.analyze(context.images, user_message)
      yield {"type":"vision", "result": vision_result.to_dict()}
      # 把视觉分析结果注入到 AgentContext 中
      context.vision_analysis = vision_result
```

---

## 八、MCP 集成（外部服务接入）

### 8.1 设计动机

P1 的工具都是 Python 写的本地工具。P2 通过 MCP 协议接入外部服务：

- 高德地图 → POI 搜索（"附近的菜市场"）
- 天气服务 → 精确天气预报
- 更多外部 API → 不需要写 Python 代码，直接配 MCP 服务器

### 8.2 实现（伪代码）

```python
# app/mcp/client.py

class MCPClient:
    """
    MCP StreamableHTTP 客户端。

    JSON-RPC 2.0 协议，通过 HTTP POST 与 MCP 服务器通信。

    参考 https://spec.modelcontextprotocol.io/
    """

    def __init__(self, endpoint: str, timeout=30.0, headers=None):
        self.endpoint = endpoint
        self._session_id = None  # MCP 会话 ID

    async def initialize(self) -> dict:
        """握手：交换协议版本和能力。"""
        return await self._send("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "CookAgent"},
        })

    async def list_tools(self) -> list[dict]:
        """获取 MCP 服务器的工具列表。"""
        result = await self._send("tools/list", {})
        return result["tools"]

    async def call_tool(self, name: str, arguments: dict) -> ToolResult:
        """调用 MCP 工具。"""
        result = await self._send("tools/call", {
            "name": name,
            "arguments": arguments,
        })
        # 解析返回的 content（支持 text/image/resource 类型）
        return self._parse_tool_result(result)


# app/mcp/setup.py

async def register_mcp_servers():
    """
    启动时注册所有 MCP 服务器。

    config.yml 中配置：
      mcp:
        servers:
          - name: "amap"
            endpoint: "https://mcp.amap.com/mcp?key=${AMAP_API_KEY}"
            enabled: true
          - name: "weather"
            endpoint: "https://weather-mcp.example.com/mcp?key=${WEATHER_KEY}"
            enabled: false
    """
    for server_config in settings.mcp.servers:
        if not server_config.enabled:
            continue
        client = MCPClient(server_config.endpoint)
        await client.initialize()
        tools = await client.list_tools()
        for tool in tools:
            mcp_tool = MCPTool(
                name=f"mcp_{server_config.name}_{tool['name']}",
                description=tool["description"],
                parameters=tool["inputSchema"],
                client=client,
            )
            AgentHub.register_tool(mcp_tool, provider="mcp")
```

---

## 九、评价系统 (Evaluation System)

### 9.1 设计动机

- 用户点赞/点踩 → 收集反馈，定位问题
- RAGAS 自动评估 → 不需要人工标注，自动评估回答质量
- 低分警报 → 及时发现问题

### 9.2 实现（伪代码）

```python
# app/evaluation/models.py

class EvaluationModel(Base):
    __tablename__ = "evaluations"
    id              UUID    PK
    user_id         UUID    FK
    query           Text    用户原始查询
    response_id     UUID    被评价的消息 ID
    rating          Int     1-5 分
    helpfulness     Bool    是否有帮助
    comment         Text    用户文字反馈
    created_at      DateTime

# 自动评估（后台异步，不阻塞用户）
class RAGEvaluationModel(Base):
    __tablename__ = "rag_evaluations"
    id              UUID    PK
    message_id      UUID
    query           Text
    context         JSONB   检索到的文档
    response        Text    LLM 回答
    faithfulness    Float   RAGAS 忠实度 (0-1)
    answer_relevancy Float  RAGAS 答案相关性 (0-1)
    status          String  pending/completed/failed
    evaluated_at    DateTime


# app/evaluation/service.py

class EvaluationService:
    """使用 RAGAS 框架自动评估 RAG 回答质量。"""

    async def evaluate(self, query, contexts, response) -> dict:
        """
        评估一次 RAG 回答。
        指标：
          - faithfulness:     回答是否忠实于检索到的文档（有没有编造）
          - answer_relevancy: 回答与用户问题的相关度
        """
        from ragas import evaluate
        from ragas.metrics import faithfulness, answer_relevancy

        dataset = Dataset.from_dict({
            "question": [query],
            "answer": [response],
            "contexts": [contexts],
        })
        result = evaluate(dataset, metrics=[faithfulness, answer_relevancy])
        return {
            "faithfulness": result["faithfulness"],
            "answer_relevancy": result["answer_relevancy"],
        }

    async def schedule_evaluation(self, message_id, query, contexts, response):
        """
        异步评估——不阻塞用户。
        在后台 asyncio.create_task() 中运行。
        """
        eval_record = await self.repo.create(
            message_id=message_id, query=query, status="pending",
        )
        asyncio.create_task(self._run_async(eval_record.id, query, contexts, response))

    async def check_alerts(self) -> list:
        """检查最近的低分评估（faithfulness < 0.3，answer_relevancy < 0.5）。
        用于调试和告警。"""
```

---

## 十、API 端点设计

```python
# app/api/v1/endpoints/diet.py

POST   /api/v1/diet/plans              # 创建饮食计划
GET    /api/v1/diet/plans              # 获取某周的计划 (?week_start=2026-06-22)
PUT    /api/v1/diet/plans/{meal_id}    # 修改某餐
DELETE /api/v1/diet/plans/{meal_id}    # 删除某餐
POST   /api/v1/diet/plans/{meal_id}/copy # 复制某餐到另一天

POST   /api/v1/diet/logs               # 记录饮食
  body: {log_date, meal_type, items: [{food_name, weight_g, calories}]}
POST   /api/v1/diet/logs/from-text     # AI 解析饮食
  body: {text: "中午吃了半碗米饭+红烧肉", image_url: null}
GET    /api/v1/diet/logs               # 按日期范围查询日记
DELETE /api/v1/diet/logs/{log_id}      # 删除记录

GET    /api/v1/diet/analysis/daily     # 今日营养汇总
GET    /api/v1/diet/analysis/weekly    # 周报
GET    /api/v1/diet/analysis/deviation # 偏差分析

GET    /api/v1/diet/preferences        # 查看饮食偏好
PUT    /api/v1/diet/preferences        # 更新饮食偏好


# app/api/v1/endpoints/evaluation.py

POST   /api/v1/evaluations             # 提交用户评价
  body: {response_id, rating: 4, helpfulness: true, comment: "很好"}
GET    /api/v1/evaluations/statistics  # 评价统计
GET    /api/v1/evaluations/alerts      # 低分警报
```

---

## 十一、配置变更

```yaml
# config.yml 新增段（在 P1 的 rag: 段之后）

# --- 12. 饮食管理配置 ---
diet:
  enabled: true
  default_calories:
    lose_weight: 1500
    gain_muscle: 2500
    balanced: 2000
  ai_parsing:
    enabled: true               # AI 解析饮食日志
    confidence_threshold: 0.6   # 低置信度标记为需人工确认

# --- 13. 视觉分析配置 ---
vision:
  enabled: true
  model: "qwen-vl-max"          # 多模态模型
  max_image_size_mb: 10
  supported_formats: ["jpg", "png", "webp"]
  food_recognition:
    enabled: true               # 食物识别

# --- 14. MCP 配置 ---
mcp:
  amap:
    enabled: false              # 默认关闭，需要 API key 才开启
    api_key: "${AMAP_API_KEY}"
  servers:                      # 用户可自定义 MCP 服务器
    - name: ""
      endpoint: ""
      enabled: false

# --- 15. 评价系统配置 ---
evaluation:
  enabled: true
  async_mode: true              # 异步评估，不阻塞用户
  sample_rate: 1.0              # 抽样率 1.0 = 全部评估
  metrics:
    - "faithfulness"
    - "answer_relevancy"
  alert_thresholds:
    faithfulness: 0.3           # 低于此值发警报
    answer_relevancy: 0.5
```

---

## 十二、实施路线

| 阶段 | 内容 | 文件数 | 预计轮数 |
|------|------|--------|----------|
| **P2-R1** | 饮食数据模型 + Repository + DietService 基础 | 5 | 1 |
| **P2-R2** | 3个饮食工具 + BaseTool 抽象 + ToolExecutor | 5 | 2 |
| **P2-R3** | SubAgent 系统 + DietPlannerSubagent 完整版 | 4 | 2 |
| **P2-R4** | AgentHub 升级 + AgentContextBuilder | 3 | 1 |
| **P2-R5** | 天气工具 + 时令食材工具 + Vision 模块 | 4 | 2 |
| **P2-R6** | MCP 客户端 + 评价系统 RAGAS | 4 | 1 |
| **P2-R7** | 后端 API 端点 + config 更新 | 4 | 2 |
| **P2-R8** | 前端 types 扩展 + API 模块 + composables | 6 | 1 |

**总计: ~35 个文件, 12 轮**

> P2-R8 前端工作详见第十四章。types 可在 P2-R1 同步开始（仅定义接口不影响运行），API 模块在对应后端路由完成后接入。

---

## 十三、设计决策备忘

| 决策 | 选择 | 原因 |
|------|------|------|
| 工具返回格式 | `ToolResult(success, data, error)` | 统一，Agent 可靠判断成功/失败 |
| 饮食工具粒度 | 一个 tool + action 参数 | 减少 Agent function calling 决策空间 |
| 子代理 vs 独立 Agent | 子代理作为工具 | 主 Agent 做编排，子代理做执行 |
| 时令食材数据 | 本地知识库 | 变化慢，不需要 API |
| 天气数据 | 高德 API | 免费，国内稳定 |
| MCP 集成 | 可选 | 默认关闭，需要时开启 |
| RAGAS 评估 | 异步 | 不阻塞用户，后台跑 |
| 上下文构建 | Builder 模式 | 链式调用，逐步组装 |

---

## 十四、前端接口配置修改

> 当前前端: Vue 3 + TypeScript + TailwindCSS，HTTP 用原生 `fetch`（无 axios），
> `API_BASE = '/api/v1'` 硬编码在 `client.ts`，通过 Vite proxy 转发到 `localhost:8000`。

### 14.1 文件变更清单

```
frontend/
├── src/
│   ├── api/
│   │   ├── client.ts              ← 不改动
│   │   ├── auth.ts                ← 不改动
│   │   ├── agent.ts               ← 不改动（SSE 事件类型由 types 扩展）
│   │   ├── conversation.ts        ← 不改动
│   │   ├── diet.ts                ← P2 新增：饮食管理 API
│   │   ├── evaluation.ts          ← P2 新增：评价反馈 API
│   │   └── vision.ts              ← P2 新增：图片分析 API
│   ├── types/
│   │   └── index.ts               ← 扩展：新增 P2 类型
│   └── composables/
│       ├── useAgent.ts            ← 扩展：处理新 SSE 事件类型
│       ├── useConversation.ts     ← 不改动
│       └── useDiet.ts             ← P2 新增：饮食管理组合式函数
└── vite.config.ts                 ← 不改动（已有代理配置足够）
```

### 14.2 新增类型定义 — `frontend/src/types/index.ts` 追加

```ts
// ===== P2 新增: 饮食管理 =====

// 饮食计划中的一餐
export interface DietPlanMeal {
  id: string
  plan_id: string
  date: string           // "2026-06-26"
  meal_type: 'breakfast' | 'lunch' | 'dinner' | 'snack'
  dish_name: string
  ingredients: string[]
  calories: number       // 估算热量 (kcal)
  recipe_id?: string     // 关联菜谱 ID
  notes?: string
}

// 一周饮食计划
export interface DietPlan {
  id: string
  user_id: string
  week_start: string     // "2026-06-26" 周一
  meals: DietPlanMeal[]
  total_calories_per_day: Record<string, number>  // {"2026-06-26": 2100, ...}
  restrictions: string[] // 饮食限制 ["egg_allergy", "low_salt"]
  created_at: string
}

// 饮食日志
export interface DietLogItem {
  id: string
  user_id: string
  date: string
  meal_type: 'breakfast' | 'lunch' | 'dinner' | 'snack'
  description: string    // 自然语言描述，如 "中午吃了番茄炒蛋和米饭"
  parsed_foods: string[] // AI 解析后的食物列表
  calories: number       // AI 估算热量
  image_url?: string     // 拍照记录
}

// 营养摘要
export interface NutritionSummary {
  start_date: string
  end_date: string
  avg_daily_calories: number
  calories_trend: 'up' | 'down' | 'stable'
  common_foods: string[]
  suggestion: string     // AI 生成的饮食建议
}

// 用户饮食偏好
export interface FoodPreference {
  user_id: string
  allergies: string[]
  diet_type: string         // "omnivore" | "vegetarian" | "vegan" | "keto"
  disliked_foods: string[]
  calorie_target: number    // 每日目标热量
}

// ===== P2 新增: 评价系统 =====

export interface EvaluationResult {
  id: string
  message_id: string
  metrics: {
    faithfulness: number       // 忠实度 0-1
    answer_relevancy: number   // 答案相关性 0-1
  }
  passed: boolean
  created_at: string
}

export interface UserFeedback {
  message_id: string
  rating: 'good' | 'bad'
  comment?: string
}

// ===== P2 新增: 视觉分析 =====

export interface VisionResult {
  food_name: string
  confidence: number
  ingredients: string[]
  estimated_calories: number
  nutrition_info: {
    protein: string
    carbs: string
    fat: string
  }
}

// ===== P2 扩展: SSE 事件类型 =====
// SSEEvent.type 新增取值:
//   'intent'       — 意图识别结果（P1 已有）
//   'sources'      — 检索来源（P1 已有）
//   'diet_plan'    — 饮食计划数据
//   'diet_log'     — 饮食记录结果
//   'nutrition'    — 营养分析摘要
//   'vision_result'— 图片识别结果
//   'evaluation'   — 评价结果

// AgentStep.type 新增取值:
//   'intent'       — 意图识别步骤
//   'diet_tool'    — 饮食工具调用
//   'vision'       — 视觉分析步骤
```

### 14.3 新增 API 模块 — `frontend/src/api/diet.ts`

```ts
// 饮食管理 API
import { apiGet, apiPost, apiDelete } from './client'
import type {
  DietPlan, DietPlanMeal, DietLogItem,
  NutritionSummary, FoodPreference,
} from '@/types'

// ---- 饮食偏好 ----
export function getFoodPreference(): Promise<FoodPreference> {
  return apiGet<FoodPreference>('/diet/preference')
}

export function updateFoodPreference(
  data: Partial<FoodPreference>
): Promise<FoodPreference> {
  return apiPost<FoodPreference>('/diet/preference', data)
}

// ---- 饮食计划 ----
export function createDietPlan(
  preferences?: string
): Promise<DietPlan> {
  return apiPost<DietPlan>('/diet/plan', { preferences })
}

export function getCurrentPlan(): Promise<DietPlan | null> {
  return apiGet<DietPlan | null>('/diet/plan/current')
}

export function getPlan(planId: string): Promise<DietPlan> {
  return apiGet<DietPlan>(`/diet/plan/${planId}`)
}

export function deletePlan(planId: string): Promise<void> {
  return apiDelete<void>(`/diet/plan/${planId}`)
}

// 替换某餐
export function replaceMeal(
  planId: string, mealId: string,
  dishName: string
): Promise<DietPlanMeal> {
  return apiPost<DietPlanMeal>(
    `/diet/plan/${planId}/meals/${mealId}/replace`,
    { dish_name: dishName }
  )
}

// ---- 饮食日志 ----
export function logDiet(
  description: string,
  imageBase64?: string
): Promise<DietLogItem> {
  return apiPost<DietLogItem>('/diet/log', {
    description,
    image_base64: imageBase64,
  })
}

export function getDietLogs(
  startDate: string, endDate: string
): Promise<DietLogItem[]> {
  return apiGet<DietLogItem[]>(
    `/diet/logs?start=${startDate}&end=${endDate}`
  )
}

// ---- 营养分析 ----
export function getNutritionSummary(
  period: 'week' | 'month' = 'week'
): Promise<NutritionSummary> {
  return apiGet<NutritionSummary>(
    `/diet/analysis?period=${period}`
  )
}
```

### 14.4 新增 API 模块 — `frontend/src/api/evaluation.ts`

```ts
// 评价系统 API
import { apiPost } from './client'
import type { UserFeedback } from '@/types'

// 提交用户评价
export function submitFeedback(
  data: UserFeedback
): Promise<{ ok: boolean }> {
  return apiPost<{ ok: boolean }>('/evaluation/feedback', data)
}
```

### 14.5 新增 API 模块 — `frontend/src/api/vision.ts`

```ts
// 视觉分析 API
import { apiPost } from './client'
import type { VisionResult } from '@/types'

// 分析食物图片 (base64)
export function analyzeFoodImage(
  imageBase64: string
): Promise<VisionResult> {
  return apiPost<VisionResult>('/vision/analyze', {
    image: imageBase64,
  })
}
```

### 14.6 新增组合式函数 — `frontend/src/composables/useDiet.ts`

```ts
// 饮食管理组合式函数
import { ref } from 'vue'
import type { DietPlan, DietLogItem, NutritionSummary, FoodPreference } from '@/types'
import {
  getFoodPreference, updateFoodPreference,
  createDietPlan, getCurrentPlan, getPlan, deletePlan, replaceMeal,
  logDiet, getDietLogs, getNutritionSummary,
} from '@/api/diet'

// 模块级共享状态
const preference = ref<FoodPreference | null>(null)
const currentPlan = ref<DietPlan | null>(null)
const plans = ref<DietPlan[]>([])
const logs = ref<DietLogItem[]>([])
const nutritionSummary = ref<NutritionSummary | null>(null)
const isLoading = ref(false)

export function useDiet() {

  // ---- 偏好 ----
  async function loadPreference() {
    try { preference.value = await getFoodPreference() } catch { /* ignore */ }
  }

  async function savePreference(data: Partial<FoodPreference>) {
    preference.value = await updateFoodPreference(data)
  }

  // ---- 计划 ----
  async function loadCurrentPlan() {
    isLoading.value = true
    try { currentPlan.value = await getCurrentPlan() } catch { /* ignore */ }
    finally { isLoading.value = false }
  }

  async function generatePlan(prefs?: string) {
    isLoading.value = true
    try {
      currentPlan.value = await createDietPlan(prefs)
    } finally { isLoading.value = false }
  }

  async function swapMeal(mealId: string, dishName: string) {
    if (!currentPlan.value) return
    const updated = await replaceMeal(currentPlan.value.id, mealId, dishName)
    // 本地替换
    const idx = currentPlan.value.meals.findIndex(m => m.id === mealId)
    if (idx !== -1) currentPlan.value.meals[idx] = updated
  }

  async function removePlan(planId: string) {
    await deletePlan(planId)
    if (currentPlan.value?.id === planId) currentPlan.value = null
  }

  // ---- 日志 ----
  async function addLog(description: string, imageBase64?: string) {
    const item = await logDiet(description, imageBase64)
    logs.value.unshift(item)
  }

  async function loadLogs(days = 7) {
    const end = new Date().toISOString().slice(0, 10)
    const start = new Date(Date.now() - days * 86400000).toISOString().slice(0, 10)
    try { logs.value = await getDietLogs(start, end) } catch { /* ignore */ }
  }

  // ---- 分析 ----
  async function loadAnalysis(period: 'week' | 'month' = 'week') {
    try { nutritionSummary.value = await getNutritionSummary(period) } catch { /* ignore */ }
  }

  return {
    preference, currentPlan, plans, logs, nutritionSummary, isLoading,
    loadPreference, savePreference,
    loadCurrentPlan, generatePlan, swapMeal, removePlan,
    addLog, loadLogs, loadAnalysis,
  }
}
```

### 14.7 useAgent 扩展 — 处理 P2 新增 SSE 事件

现有 `useAgent.ts` 的 `sendMessage` 中 switch-case 只处理了 6 种事件类型，P2 需新增以下 case：

```ts
// 在 useAgent.ts sendMessage() 的 switch (event.type) 中追加:

  case 'intent':
    // P1 已有，记录意图识别结果
    currentSteps.value.push({
      type: 'intent',
      content: `意图: ${event.intent_type} (${(event.confidence * 100).toFixed(0)}%)`,
      step_number: currentSteps.value.length,
    })
    break

  case 'sources':
    // P1 已有，附加到当前流式消息的元数据（暂存到 streamingSources）
    // 在 done 时一起挂到 message.sources
    if (event.sources) {
      streamingSources.value = event.sources
    }
    break

  case 'diet_plan':
    // P2 新增：Agent 返回饮食计划数据
    // 前端可在侧边栏/弹窗中展示结构化计划
    dietPlanData.value = event.data
    break

  case 'nutrition':
    // P2 新增：营养分析结果
    nutritionData.value = event.data
    break

  case 'vision_result':
    // P2 新增：图片识别结果
    visionData.value = event.data
    break
```

同时需要在 `useAgent.ts` 中新增两个响应式变量：

```ts
// useAgent.ts 模块级新增
const streamingSources = ref<Source[]>([])   // P1 sources 暂存
const dietPlanData = ref<DietPlan | null>(null)  // P2 饮食计划
const nutritionData = ref<NutritionSummary | null>(null)  // P2 营养分析
const visionData = ref<VisionResult | null>(null)  // P2 视觉识别
```

并在 sendMessage 的 `done` case 中把 `streamingSources` 挂到消息上：

```ts
case 'done':
  if (streamingContent.value) {
    messages.value.push({
      id: event.message_id || `msg-${Date.now()}`,
      role: 'assistant',
      content: streamingContent.value,
      created_at: new Date().toISOString(),
      thoughts: [...currentSteps.value],
      sources: [...streamingSources.value],  // ← P2 新增
    })
  }
  streamingContent.value = ''
  currentSteps.value = []
  streamingSources.value = []  // ← P2 新增：重置
  break
```

### 14.8 前端-后端端点对照表

| 前端 API 函数 | 方法 | 路径 | 后端路由器 | P2 新增? |
|---|---|---|---|---|
| `login/register/getMe` | POST | `/auth/*` | `app/auth/router.py` | — |
| `listAgentSessions` | GET | `/agent/sessions` | `app/agent/router.py` | — |
| `streamAgentChat` | POST (SSE) | `/agent/chat` | `app/agent/router.py` | — |
| `streamConversation` | POST (SSE) | `/conversations/{id}/chat` | `app/conversation/router.py` | — |
| `getFoodPreference` | GET | `/diet/preference` | `app/diet/router.py` | ✅ |
| `updateFoodPreference` | POST | `/diet/preference` | `app/diet/router.py` | ✅ |
| `createDietPlan` | POST | `/diet/plan` | `app/diet/router.py` | ✅ |
| `getCurrentPlan` | GET | `/diet/plan/current` | `app/diet/router.py` | ✅ |
| `getPlan` | GET | `/diet/plan/{id}` | `app/diet/router.py` | ✅ |
| `deletePlan` | DELETE | `/diet/plan/{id}` | `app/diet/router.py` | ✅ |
| `replaceMeal` | POST | `/diet/plan/{id}/meals/{mid}/replace` | `app/diet/router.py` | ✅ |
| `logDiet` | POST | `/diet/log` | `app/diet/router.py` | ✅ |
| `getDietLogs` | GET | `/diet/logs?start=&end=` | `app/diet/router.py` | ✅ |
| `getNutritionSummary` | GET | `/diet/analysis?period=` | `app/diet/router.py` | ✅ |
| `submitFeedback` | POST | `/evaluation/feedback` | `app/evaluation/router.py` | ✅ |
| `analyzeFoodImage` | POST | `/vision/analyze` | `app/vision/router.py` | ✅ |

### 14.9 前端文件修改汇总

| 文件 | 操作 | 内容 |
|------|------|------|
| `src/types/index.ts` | **扩展** | 新增 8 个接口，SSEEvent/AgentStep 类型扩展 |
| `src/api/diet.ts` | **新建** | 饮食管理 10 个 API 函数 |
| `src/api/evaluation.ts` | **新建** | 评价反馈 1 个 API 函数 |
| `src/api/vision.ts` | **新建** | 图片分析 1 个 API 函数 |
| `src/composables/useDiet.ts` | **新建** | 饮食管理响应式状态 + 10 个方法 |
| `src/composables/useAgent.ts` | **扩展** | 新 SSE 事件处理 + sources/nutrition/vision 状态 |
| `src/api/client.ts` | 不改动 | 通用请求封装保持不变 |
| `vite.config.ts` | 不改动 | 现有 `/api` 代理足够 |
| `src/api/auth.ts` | 不改动 | — |
| `src/api/agent.ts` | 不改动 | SSE 事件由 types 和 composable 处理 |
| `src/api/conversation.ts` | 不改动 | — |

### 14.10 实施建议

1. **P2-R1~R2 阶段先改 types**：新增接口定义，SSEEvent 的 `type` 用 `string` 已能兼容新事件类型，不急于改 composable
2. **P2-R3~R5 阶段补 API 模块**：`diet.ts`、`vision.ts`、`evaluation.ts` 与对应后端路由同步开发
3. **P2-R6~R7 阶段改 composable**：`useDiet.ts` 新建，`useAgent.ts` 追加事件处理
4. **vite.config.ts 不需要改**：所有 P2 端点都在 `/api/v1/*` 下，现有代理配置已覆盖
