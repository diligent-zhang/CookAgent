# CookAgent P1-P2 实施计划

> 日期: 2026-06-21 | 状态: 设计完成，待实施

---

## 一、P1 目标：意图识别 + Agent 重构 + RAG 增强

### 1.1 概述

P1 的核心是让系统从"用户说什么就直接搜什么"升级为"理解用户意图后选择合适的专家处理"。

```
当前 (P0完成):                  P1 目标:
  用户消息                        用户消息
    │                               │
    ▼                               ▼
  RAG检索 ← 固定路径            IntentDetector ──→ 意图分类
    │                               │
    ▼                          ┌────┼────┬──────────┐
  LLM生成                       ▼    ▼    ▼          ▼
                            菜谱专家 烹饪帮助 饮食规划 闲聊
                               │    │    │          │
                               ▼    ▼    ▼          ▼
                            RAG  技巧库  Diet工具  直接LLM
                               │    │    │          │
                               └────┴────┴──────────┘
                                        │
                                        ▼
                                   Reranker 精排
                                        │
                                        ▼
                                    LLM 生成
```

### 1.2 文件变更规划

```
新建:
  app/conversation/intent.py          # IntentDetector 意图分类器
  app/conversation/query_rewriter.py  # QueryRewriter 查询改写
  app/conversation/prompts.py         # LLM 提示词模板（意图分类+改写）

  app/agent/registry/
    __init__.py
    hub.py                            # AgentRegistry 注册中心

  app/agent/agents/
    __init__.py
    base.py                           # BaseAgent 抽象基类
    default.py                        # GeneralAgent 通用对话

  app/agent/subagents/
    __init__.py
    base.py                           # BaseSubAgent 抽象基类
    registry.py                       # SubAgentRegistry 子代理注册
    builtin/
      __init__.py
      recipe_master.py                # RecipeMasterSubAgent 菜谱专家
      diet_planner.py                 # DietPlannerSubAgent 饮食规划(骨架)

  app/rag/rerankers/
    __init__.py
    base.py                           # BaseReranker 抽象类
    dashscope_reranker.py             # DashScope Reranker 实现

修改:
  app/agent/__init__.py               # init_agent_module 改为注册式
  app/agent/service.py                # AgentService 使用 AgentRegistry 路由
  app/conversation/service.py         # 集成 IntentDetector + QueryRewriter
  app/rag/pipeline/retrieval.py       # 检索后接入 Reranker
  app/database/models.py              # AgentSession 增加压缩摘要字段
```

---

## 二、P1 模块详细设计

### 2.1 IntentDetector — 意图分类器

**文件：** `app/conversation/intent.py`

```
这段代码做意图识别——用 fast LLM 把用户输入分类到 5 种意图之一。

输入: "帮我找几个快手的减脂菜"
输出: Intent(type="recipe_search", confidence=0.95, params={"tags": ["快手", "减脂"]})

5 种意图:
  recipe_search   → 路由到 RecipeMasterAgent
  cooking_help    → 路由到 GeneralAgent + RAG(技巧库)
  diet_plan       → 路由到 DietPlannerAgent
  ingredient_ask  → 路由到 GeneralAgent + RAG(菜谱)
  general_chat    → 路由到 GeneralAgent

prompts.py 中的分类提示词:
  系统提示词要求 LLM 返回 JSON:
  {
    "intent": "recipe_search|cooking_help|diet_plan|ingredient_ask|general_chat",
    "confidence": 0.0-1.0,
    "keywords": ["提取的关键词"],
    "filters": {"category": null, "difficulty": null, "tags": []}
  }

缓存策略:
  Redis key: intent:{md5(query)}
  TTL: 3600 秒
  用途: 同一个问题翻页/重试时不重复检测

降级:
  LLM 超时(>2s) → 默认走 recipe_search
  LLM 返回格式错误 → JSON 解析失败 → 默认 recipe_search
```

### 2.2 QueryRewriter — 查询改写

**文件：** `app/conversation/query_rewriter.py`

```
这段代码在 RAG 检索之前改写用户查询，提升检索命中率。

改写目标:
  1. 补全口语化表达: "上次那个排骨" → "排骨菜谱"
  2. 展开缩写/别名: "鱼香rose" → "鱼香肉丝"
  3. 提取关键特征: "我想要做起来快一点的减脂午餐"
     → "快速 简易 午餐 低脂 菜谱"

为什么需要改写？
  Milvus 的 Dense 向量搜索依赖语义相似度。"上次那个排骨的怎么做来着"
  和菜谱文档中的 "排骨做法" 在向量空间中可能不够近。
  改写后去掉噪音词（上次/那个/来着），增加关键词，检索更精准。

实现:
  fast LLM (qwen-plus) + 专门的改写提示词
  仅在 recipe_search / cooking_help 意图时触发
  max_output: 200 字符（改写后也不要太长）

配置控制:
  conversation.query_rewriting.enabled: true/false
```

### 2.3 AgentRegistry — Agent 注册中心

**文件：** `app/agent/registry/hub.py`

```
这段代码实现 Agent 的可插拔注册机制。

当前问题:
  AgentService 通过硬编码创建 ReActAgent，新增 Agent 类型需要改大量代码。

目标:
  AgentRegistry 管理所有 Agent，IntentDetector 的结果直接路由到对应 Agent。

interface AgentRegistry:
  register(name: str, agent_class: Type[BaseAgent], config: dict) → None
  get(name: str) → BaseAgent
  match(intent: Intent) → BaseAgent    # 意图 → Agent 映射
  list() → List[str]                   # 列出所有已注册 Agent

注册时机:
  在 init_agent_module() 中集中注册:
  registry = AgentRegistry()
  registry.register("recipe_master", RecipeMasterAgent, {...})
  registry.register("diet_planner", DietPlannerAgent, {...})
  registry.register("general", GeneralAgent, {...})

配置映射:
  config.yml 中可控制启用哪些 Agent:
  agent:
    enabled_agents: ["recipe_master", "general"]  # diet_planner 未就绪时关闭
```

### 2.4 BaseAgent — 统一 Agent 接口

**文件：** `app/agent/agents/base.py`

```
这段代码定义所有 Agent 必须遵守的接口契约。

为什么需要抽象基类？
  当前 ReActAgent 是一个具体类，没有接口约束。
  引入多种 Agent 后，调用方需要知道每个 Agent 的方法签名。
  BaseAgent 统一了接口，调用方只依赖抽象，不依赖具体。

class BaseAgent(ABC):
    name: str                    # 唯一标识符
    description: str             # 用途说明
    intent_match: List[str]      # 匹配哪些意图类型

    @abstractmethod
    async def execute(
        self,
        user_message: str,
        context: AgentContext,
    ) -> AgentResult:
        """执行 Agent 逻辑并返回结果。流式输出通过 context 中的 callback 实现。"""

AgentContext 数据结构:
  user_id: str
  session_id: str
  intent: Intent
  rewritten_query: str | None
  compressed_context: dict | None   # ContextManager.build_context 的输出
  stream_callback: Callable         # SSE 回调

AgentResult 数据结构:
  content: str                     # 最终回答文本
  tool_calls_made: int             # 触发了几次工具调用
  steps: List[AgentStep]           # 中间推理步骤（用于前端展示）
  sources: List[Source]            # 引用的数据源
```

### 2.5 RecipeMasterAgent — 菜谱专家

**文件：** `app/agent/subagents/builtin/recipe_master.py`

```
这段代码是菜谱检索+生成的专业 SubAgent。

工作流:
  1. 接收 rewritten_query + 用户偏好（从 ContextManager 上下文提取）
  2. 调用 RAGService.retrieve(query)
     → Milvus Hybrid Search → MetadataFilter(按难度/类别/烹饪时间)
     → Reranker.rerank(top_k=9 → top_n=5)
     → Small-to-Large 父文档还原
  3. 构建 Prompt: system_prompt + compressed_summary + 检索结果 + user_query
  4. LLM.generate(prompt) → SSE stream

与现有 ReActAgent 的关系:
  RecipeMasterAgent 替代了 ReActAgent 中 search_recipes + get_recipe_detail
  这两个工具的使用场景。不需要 ReAct 循环中的 Thought/Action/Observation，
  直接 RAG → Generate 更高效（减少 2-3 次 LLM 调用）。
  
  ReAct 循环保留给需要多步推理的场景（如"根据我冰箱里的食材推荐三菜一汤"）。
```

### 2.6 Reranker — 检索结果精排

**文件：** `app/rag/rerankers/dashscope_reranker.py`

```
这段代码对 Milvus 检索结果进行二次精排。

为什么需要 Reranker？
  Milvus 的 Dense+Sparse Hybrid Search 是粗排——基于向量距离和 BM25 打分。
  粗排关注"长得像"，精排关注"语义上是否真正相关"。
  
  典型案例:
    查询: "有什么菜适合老人吃"
    粗排可能返回: "老人头菌炒肉"（"老人"字面匹配，BM25 给高分）
    精排后会降低此类结果，提升"软烂易消化"相关菜谱。

实现:
  DashScope gte-rerank 模型（或 dashscope.TextReRank API）
  输入: query + 候选文档列表
  输出: 按相关性重新排序的文档列表

配置:
  rag:
    reranker:
      enabled: true
      model: "gte-rerank"
      top_n: 5        # 精排后保留 Top5

位置:
  在 retrieval.py 的 hybrid_search 之后、Small-to-Large 之前调用
  → hybrid_search(top_k=9) → reranker.rerank(top_n=5) → Small-to-Large(5)
```

### 2.7 MetadataFilter — 元数据过滤

**文件：** 扩展现有 `app/rag/pipeline/retrieval.py`

```
这段代码在 Milvus 检索时利用标量字段过滤结果。

过滤维度:
  difficulty:      1-5 星（用户说"简单的菜" → difficulty <= 2）
  category:        菜系分类（用户说"汤类" → category = "soup"）
  cooking_time_mins: 估算烹饪时间（用户说"20分钟内" → cooking_time <= 20）
  tags:            饮食标签（"低脂""高蛋白""素食"）

实现方式:
  Milvus search 的 expr 参数支持标量过滤表达式:
    expr = 'difficulty <= 2 and category == "soup"'
  过滤条件由 IntentDetector 提取的 params 中获取。

为什么在 Milvus 侧过滤而不是 Python 侧？
  Milvus 侧过滤后只返回符合条件的向量，减少了网络传输和不必要的计算。
  如果先取 Top 9 再 Python 过滤，可能符合条件的不足 5 条。
```

---

## 三、P2 目标：饮食规划 + Web 搜索 + 评价系统

### 3.1 概述

P2 从"烹饪助手"扩展到"饮食管理"，加入专业领域功能。

```
P1 完成:                         P2 新增:
  菜谱搜索                         饮食规划
  烹饪技巧                          ├─ 周食谱生成
  通用对话                          ├─ 饮食日志解析
  意图路由                          └─ 营养分析报告
                                  联网搜索 (WebSearchTool)
                                  用户评价 (Evaluation)
```

### 3.2 文件变更规划

```
新建:
  app/diet/
    __init__.py
    service.py                        # DietService 核心业务
    database/
      __init__.py
      models.py                       # DietPlan, DietLog ORM
      repository.py                   # 饮食数据 CRUD
    tools/
      __init__.py
      diet_plan_tool.py               # @tool 生成饮食计划
      diet_log_tool.py                # @tool 记录饮食日志
      diet_analysis_tool.py           # @tool 营养分析
    prompts/
      __init__.py
      log_parsing.py                  # 饮食日志 LLM 解析提示词

  app/api/v1/endpoints/diet.py        # /diet/* API 端点
  app/api/v1/endpoints/evaluation.py  # /evaluations API 端点

  app/agent/tools/web_search.py       # @tool 联网搜索菜谱

修改:
  app/database/models.py              # 追加 DietPlan, DietLog, Evaluation 表
  app/agent/tools/__init__.py          # 注册 web_search, diet_plan 等新工具
  config.yml                          # 追加 diet + web_search + evaluation 配置段
```

### 3.3 饮食规划系统

**文件：** `app/diet/`

```
这段代码实现完整的饮食规划功能。

三个核心工具:
  1. generate_diet_plan(goal, days, calories_per_day, preferences)
     输入: "减脂", 7天, 1500卡/天, "不要牛肉"
     输出: 一周食谱 JSON [{day, meals: [{type, dish, calories, reason}]}]
     实现: LLM + RAG 检索低卡菜谱 → 组合成合理的每日三餐

  2. log_meal(description, meal_type, image_url?)
     输入: "中午吃了红烧肉盖饭，大概一碗"
     输出: {foods: [{name: "红烧肉", portion: "150g", calories: 350}], total: 750}
     实现: LLM 解析自然语言描述 → 估算分量和卡路里

  3. analyze_nutrition(user_id, days)
     输入: 分析最近 7 天的饮食
     输出: 每日热量趋势图数据 + 营养均衡评估 + 改进建议
     实现: 聚合 diet_logs 数据 → LLM 分析

数据模型:
  diet_plans:
    id: UUID
    user_id: FK
    goal: "lose_weight" | "gain_muscle" | "balanced"
    days: Integer
    content: JSONB          # 完整计划的 JSON
    calories_target: Integer
    created_at: DateTime

  diet_logs:
    id: UUID
    user_id: FK
    meal_type: "breakfast" | "lunch" | "dinner" | "snack"
    description: Text       # 用户原文
    parsed_foods: JSONB     # LLM 解析后的结构化数据
    calories: Integer       # 估算卡路里
    logged_at: Date

API 端点:
  POST   /api/v1/diet/plans          # 创建饮食计划
  GET    /api/v1/diet/plans          # 列表
  GET    /api/v1/diet/plans/{id}     # 详情
  DELETE /api/v1/diet/plans/{id}     # 删除
  POST   /api/v1/diet/logs           # 记录饮食
  GET    /api/v1/diet/logs           # 列表（按日期范围筛选）
  GET    /api/v1/diet/analysis       # 营养分析报告

前端页面（后续开发）:
  /diet — 饮食管理页
    ├─ 饮食计划 Tab（创建/查看/编辑周计划）
    ├─ 饮食日志 Tab（记录/查看每日饮食）
    └─ 分析报告 Tab（热量趋势图 + 营养建议）
```

### 3.4 Web 搜索工具

**文件：** `app/agent/tools/web_search.py`

```
这段代码提供联网搜索菜谱的能力。

触发条件:
  当 RAG 检索结果的 score 低于阈值（如 < 0.3），
  说明本地菜谱库中没有相关内容，Agent 自动调用 web_search。

@tool web_search(query: str) -> List[SearchResult]:
  {
    "title": "红烧肉的做法 - 下厨房",
    "snippet": "...",
    "url": "https://...",
    "source": "web"
  }

实现选项:
  A. DashScope 搜索 API（阿里云内置，不需要额外 key）
  B. Bing Search API（质量好，需要 Azure key）
  C. 自建搜索（SerpAPI 等）

推荐先 A 再 B：DashScope 搜索 API 和现有 LLM 用同一个 key，
无额外费用和配置，快速上线。

配置:
  agent:
    web_search:
      enabled: false         # 默认关闭
      provider: "dashscope"  # dashscope | bing
      max_results: 3
      timeout: 5             # 秒
```

### 3.5 评价/反馈系统

**文件：** `app/api/v1/endpoints/evaluation.py`

```
这段代码收集用户对回答的反馈，用于评估和改进 RAG 效果。

数据模型:
  evaluations:
    id: UUID
    user_id: FK
    query: Text             # 用户原始查询
    response_id: UUID       # 被评价的 assistant 消息 ID
    rating: SmallInt        # 1-5 分
    helpfulness: SmallInt   # 是否有帮助: 0/1
    comment: Text           # 用户文字反馈
    created_at: DateTime

API:
  POST /api/v1/evaluations
    body: {response_id, rating, helpfulness, comment?}

前端:
  在每条 assistant 回答下方增加 👍/👎 按钮 + 可选文字反馈输入框。

数据用途:
  - 统计: 哪些类型的查询评分低 → 针对性优化
  - 反馈收集: 低分回答的 comment → 了解问题根源
  - AB 测试: 对比 Reranker 开启/关闭前后的评分变化
```

---

## 四、配置变更汇总

```yaml
# config.yml 新增段（在 context 段之后追加）

# --- 9. 意图识别与查询改写 ---
conversation:
  intent_detection:
    enabled: true
    cache_ttl: 3600               # 意图缓存时间（秒）
    fallback_intent: "recipe_search"  # LLM 超时/出错时的默认意图
    timeout: 2                    # 意图检测超时（秒）

  query_rewriting:
    enabled: true
    max_rewrite_length: 200

# --- 10. Agent 配置 ---
agent:
  enabled_agents:
    - "recipe_master"
    - "general"
    # - "diet_planner"            # P2 启用
  web_search:
    enabled: false
    provider: "dashscope"
    max_results: 3
    timeout: 5

# --- 11. RAG 增强配置 ---
rag:
  reranker:
    enabled: true
    model: "gte-rerank"
    top_n: 5
  metadata_filter:
    enabled: true

# --- 12. 饮食规划配置（P2） ---
diet:
  enabled: false                  # P2 时才改为 true
  default_calories:
    lose_weight: 1500
    gain_muscle: 2500
    balanced: 2000

# --- 13. 评价系统配置（P2） ---
evaluation:
  enabled: false                  # P2 时才改为 true
  require_auth: true
```

---

## 五、实施步骤

### P1 — 预计 5 轮

| 轮次 | 内容 | 文件数 |
|------|------|--------|
| P1-R1 | IntentDetector + QueryRewriter + prompts.py | 3 新建 |
| P1-R2 | AgentRegistry + BaseAgent + GeneralAgent | 4 新建 |
| P1-R3 | RecipeMasterAgent + DietPlannerAgent(骨架) | 3 新建 |
| P1-R4 | Reranker + MetadataFilter | 2 新建 + 1 修改 |
| P1-R5 | 集成串联 + AgentService 改造 | 3 修改 |

### P2 — 预计 4 轮

| 轮次 | 内容 | 文件数 |
|------|------|--------|
| P2-R1 | DietService + DietPlan/Log 模型 + Repository | 4 新建 |
| P2-R2 | DietTools + DietPrompts + Diet API 端点 | 4 新建 |
| P2-R3 | WebSearchTool + Evaluation API + 模型 | 3 新建 |
| P2-R4 | config.yml 更新 + 集成串联 + 前端页面骨架 | 2 修改 |
