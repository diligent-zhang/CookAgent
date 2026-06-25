# CookAgent 架构升级设计文档

> 日期: 2026-06-21 | 目标: P0-P2 功能模块逐步补齐

---

## 一、当前架构

### 1.1 整体拓扑

```
┌──────────────┐        ┌───────────────────────────────────┐
│   Frontend   │  SSE   │  FastAPI (app/main.py)             │
│   Vue 3 + TS │◀──────▶│                                    │
│   Tailwind   │        │  /api/v1/auth/*        注册/登录   │
│   双模式:     │        │  /api/v1/conversations/*  RAG对话  │
│   Agent/RAG  │        │  /api/v1/agent/*        Agent对话  │
└──────────────┘        │                                    │
                        │  内部组件:                          │
                        │  ├─ AuthService (bcrypt + JWT)      │
                        │  ├─ ConversationService             │
                        │  ├─ AgentService → ReActAgent       │
                        │  │   └─ Tools×6 (recipe/            │
                        │  │      nutrition/user)             │
                        │  ├─ RAGService                      │
                        │  │   ├─ Embedding(DashScope v3)     │
                        │  │   ├─ Milvus Hybrid(Dense+Sparse) │
                        │  │   ├─ Small-to-Large PostProcess  │
                        │  │   └─ DualCache(Redis+Milvus)     │
                        │  └─ LLMProvider → DashScope         │
                        │     (qwen-plus / qwen-max)         │
                        │                                    │
                        │  数据层:                            │
                        │  PostgreSQL / Milvus / Redis        │
                        └───────────────────────────────────┘
```

### 1.2 当前请求流程

```
POST /api/v1/conversations/{id}/chat  (或 /agent/chat)

  → get_current_user (JWT验证)
  → ConversationService.chat() / AgentService.chat()
     → save user message to DB
     → RAGService.retrieve(query)
        → Embedding → Milvus Hybrid Search → Cache Check → Small-to-Large
     → LLMProvider.stream(prompt + context)
        → SSE → Frontend
     → save assistant message to DB
```

**核心问题:**
1. 没有请求级别的安全防护（无速率限制、无输入检测）
2. Agent 是单一 ReActAgent，无法扩展多专业子代理
3. 对话没有压缩机制，长对话会超出 token 限制
4. 没有意图识别，所有请求走相同路径
5. 检索结果没有 Reranker 精排

---

## 二、目标架构 (P0-P2)

### 2.1 总览

```
                              ┌─────────────────────────────────────────────┐
                              │              中间件层 (P0)                    │
                              │  RateLimiter → PromptGuard → AuditLog       │
                              │  (Redis)      (规则引擎)    (PG)            │
                              │                                              │
                              │  ┌───────────────────────────────────────┐   │
                              │  │            意图识别层 (P1)             │   │
                              │  │  IntentDetector ──→ QueryRewriter     │   │
                              │  │  (LLM fast)          (LLM fast)       │   │
                              │  └───────────────────────────────────┬───┘   │
                              │                                      │       │
   ┌──────────┐   SSE    ┌────┴──────────────────────────────────────┴───┐   │
   │ Frontend │◄────────▶│              Agent 调度层 (P1)                │   │
   │          │          │  AgentRegistry ──→ AgentRouter                │   │
   │ - 聊天页  │          │   ├─ RecipeMasterAgent (菜谱检索+生成)        │   │
   │ - Agent页│          │   ├─ DietPlannerAgent (饮食规划) [NEW]        │   │
   │ - 饮食页  │          │   └─ GeneralAgent (通用对话)                  │   │
   │   [NEW]  │          │                                               │   │
   └──────────┘          │  ┌───────────────────────────────────────┐    │   │
                         │  │          上下文管理 (P0)              │    │   │
                         │  │  ContextManager ←→ Compressor(LLM)    │    │   │
                         │  └───────────────────────────────────────┘    │   │
                         │                                               │   │
                         │  ┌───────────────────────────────────────┐    │   │
                         │  │          RAG 增强 (P1)                │    │   │
                         │  │  Retrieval → MetadataFilter → Reranker│    │   │
                         │  │            WebSearchTool [NEW]        │    │   │
                         │  └───────────────────────────────────────┘    │   │
                         │                                               │   │
                         │                      LLMProvider (不变)       │   │
                         │                                               │   │
                         │  数据层: PG / Milvus / Redis                   │   │
                         │  + diet_plans, diet_logs, evaluations [NEW]   │   │
                         └──────────────────────────────────────────────┘
```

### 2.2 完整请求流程 (P2完成后)

```
用户消息
  │
  ▼
[1] RateLimiter         ← Redis sliding window       (P0)
    检查: user_id + endpoint
    超出限制 → 429 Too Many Requests
  │
  ▼
[2] PromptGuard         ← 规则引擎                    (P0)
    检查: 注入攻击 / 越狱提示词 / 敏感内容
    拦截 → 400 Bad Request + 安全日志
  │
  ▼
[3] IntentDetector      ← LLM fast (qwen-plus)       (P1)
    分类: recipe_search / diet_plan / cooking_help / general_chat
  │
  ▼
[4] QueryRewriter       ← LLM fast (qwen-plus)       (P1)
    优化: 补全上下文 / 消除歧义 / 关键词展开
  │
  ▼
[5] ContextManager      ← Redis + LLM                 (P0)
    加载: 压缩后的历史摘要 + 最近K轮对话
    如超出token限制 → Compressor进行LLM摘要压缩
  │
  ▼
[6] AgentRouter         根据意图选择Agent              (P1)
     ├─ recipe_search → RecipeMasterAgent
     │    ├─ RAG.retrieve(rewritten_query)
     │    ├─ MetadataFilter(按难度/类别)
     │    ├─ Reranker.rerank(results)
     │    └─ LLM.generate(context + results)
     │
     ├─ diet_plan → DietPlannerAgent
     │    ├─ diet_tools.analyze(user_log)
     │    ├─ diet_tools.generate_plan(goal)
     │    └─ LLM.generate(plan)
     │
     └─ general_chat → GeneralAgent
          ├─ (可选) WebSearchTool.search(query)   (P2)
          └─ LLM.generate(context)
  │
  ▼
[7] OutputGuard         ← 规则引擎                    (P0)
    检查: 有害内容 / 不安全的烹饪建议
  │
  ▼
[8] SSE Stream → Frontend
  │
  ▼
[9] AuditLog            ← PG异步写入                   (P0)
    记录: user, action, intent, tokens, duration
```

---

## 三、P0 详细设计 — 安全防护 + 上下文管理

### 3.1 Security 模块 (`app/security/`)

```
app/security/
  __init__.py               # 模块导出
  middleware/
    __init__.py
    rate_limiter.py          # Redis滑动窗口速率限制
    prompt_guard.py          # 输入Prompt检测
  guard.py                   # GuardManager 统一入口
  rules.py                   # 内置规则集
  audit.py                   # 审计日志
  models.py                  # AuditLog ORM
```

#### 3.1.1 RateLimiter 设计

```
策略: Redis Sliding Window
Key:  rate_limit:{user_id}:{endpoint}
窗口: 每分钟/每小时 可配置

config.yml 新增:
  security:
    rate_limit:
      enabled: true
      default: "60/minute"          # 默认每用户60次/分钟
      endpoints:
        "/api/v1/agent/chat": "10/minute"     # Agent问答更贵,限制更严
        "/api/v1/conversations/*/chat": "20/minute"
        "/api/v1/auth/login": "5/minute"      # 登录防爆破
        "/api/v1/auth/register": "3/minute"

HTTP 429 响应:
  {"detail": "Too many requests", "retry_after": 30}
```

**为什么用 Sliding Window 而非 Fixed Window 或 Token Bucket?**
- Sliding Window 平滑性好，不会出现窗口边界突发流量
- Redis ZSET 实现，原子操作，多进程安全
- 对用户感知友好（不会出现"恰好跨分钟被双重限制"的情况）

#### 3.1.2 PromptGuard 设计

```
两层检测:

Layer 1 — 规则引擎 (零延迟):
  - 正则匹配: SQL注入/命令注入/路径遍历
  - 关键词黑名单: 敏感话题/越狱关键词
  - 长度限制: 单条消息 max_length = 4000 字符
  - 编码检测: 检测Base64/Unicode混淆攻击

Layer 2 — LLM检测 (按需开启，高成本):
  - 调用 fast LLM 判断是否存在提示词注入
  - 仅当 Layer 1 产生不确定结果时触发
  - 结果缓存(PG)避免重复检测

配置:
  security:
    prompt_guard:
      enabled: true
      max_message_length: 4000
      deny_patterns: ["prompt_injection", "jailbreak", "dos_attempt"]
      llm_check:
        enabled: false          # 默认关闭LLM检测,降低成本和延迟
        threshold: 0.7
```

**为什么两层而不是全部LLM判定?**
- 规则引擎零延迟、零成本，能拦截90%+的攻击
- LLM判断有延迟(200ms+)和API成本
- 两层结合兼顾性能和安全性

#### 3.1.3 AuditLog 设计

```
audit_logs 表:
  id            UUID
  user_id       UUID FK
  action        VARCHAR(50)     # chat/agent/register/login
  endpoint      VARCHAR(200)
  intent        VARCHAR(50)     # 意图类型(后续P1使用)
  input_summary VARCHAR(500)    # 用户输入前100字符
  status        VARCHAR(20)     # allowed/blocked/error
  block_reason  VARCHAR(200)    # 拦截原因
  ip_address    VARCHAR(45)
  user_agent    VARCHAR(500)
  duration_ms   INTEGER
  token_count   INTEGER
  created_at    TIMESTAMP

记录时机: 请求结束后异步写入，不阻塞主流程
实现方式: FastAPI BackgroundTasks
```

### 3.2 上下文管理模块 (`app/context/`)

```
app/context/
  __init__.py
  manager.py          # ContextManager 主逻辑
  compress.py         # Compressor — LLM摘要压缩
  models.py           # 压缩记录(可选持久化)
```

#### 3.2.1 ContextManager 设计

```
核心问题:
  LLM 有 token 上限 (qwen-max: 8192 tokens)
  长对话超出限制后,需要截断或压缩历史

策略: 滑动窗口 + 摘要压缩

┌─────────────────────────────────────────────┐
│ 对话历史 (按时间从旧到新)                      │
│                                              │
│ [msg1] [msg2] ... [msgN-5] [msgN-4..msgN]   │
│ ◄── 压缩区 ──►            ◄── 保留区 ──►     │
│                                              │
│ 压缩区: LLM摘要为一个 compressed_summary       │
│ 保留区: 最近 K 轮对话保持原文 (K=5 可配置)       │
└─────────────────────────────────────────────┘

算法:
  1. 计算历史总 tokens (粗略估计: 1 char ≈ 0.5 token 中文)
  2. 如果 total_tokens < threshold (默认 max_tokens * 0.6):
     返回全部历史
  3. 否则:
     a. 保留最近K轮原文
     b. 剩余历史转化为 LLM 摘要(仅首次压缩,后续增量更新)
     c. 构建最终上下文:
        [System Prompt] + [CompressedSummary] + [Recent Messages] + [Current Query]
```

#### 3.2.2 Compressor 设计

```
增量压缩策略(减少LLM调用成本):

首次触发:
  输入: N条历史消息
  输出: compressed_summary (约200-500 tokens)
  成本: 1次 LLM fast 调用

后续触发:
  输入: 旧摘要 + 新增消息
  输出: 更新后的摘要
  成本: 1次 LLM fast 调用 (输入更小)

存储: conversations 表新增字段
  compressed_summary TEXT
  compressed_count INTEGER   # 已压缩的消息数

阈值触发:
  - 对话超过10轮时触发首次压缩
  - 后续每5轮增量更新一次
```

**为什么用滑动窗口+摘要而非纯截断?**
- 纯截断丢失早期上下文（用户可能在对话开头提到关键信息如过敏食材）
- 摘要保留关键信息的同时大幅减少token占用
- 增量更新减少重复压缩成本

---

## 四、P1 详细设计 — 意图识别 + Agent重构 + RAG增强

### 4.1 意图识别模块 (`app/conversation/`)

```
app/conversation/              # 扩展现有conversation模块
  intent.py                    # IntentDetector
  query_rewriter.py            # QueryRewriter
  prompts.py                   # LLM提示词模板
```

#### 4.1.1 IntentDetector

```
意图分类:
  recipe_search    # 搜索菜谱 (如"红烧肉怎么做")
  cooking_help     # 烹饪技巧 (如"油温怎么判断")
  diet_plan        # 饮食规划 (如"帮我制定一周的减脂食谱")
  ingredient_ask   # 食材问题 (如"鸡蛋和番茄能一起吃吗")
  general_chat     # 闲聊

实现:
  使用 fast LLM (qwen-plus) + structured output (JSON mode)
  每次意图检测: ~200ms, ~100 tokens

Fallback: 如果LLM不可用/超时,默认走 recipe_search

缓存: Redis key = intent:{hash(query)}  TTL=3600
      相同问题不重复检测(如"翻页/重试"场景)
```

#### 4.1.2 QueryRewriter

```
作用: 将用户自然语言查询优化为更适合检索的格式

示例:
  输入: "上次那个排骨的怎么做来着"
  输出: "排骨的做法"

  输入: "有没有快手的午餐"
  输出: "快速 简易 午餐 菜谱 30分钟内"

实现:
  fast LLM (qwen-plus)
  仅在 recipe_search / cooking_help 意图时触发

配置:
  conversation:
    query_rewriting:
      enabled: true
      max_rewrite_length: 200
```

### 4.2 Agent 系统重构 (`app/agent/`)

```
app/agent/                     # 重构现有agent模块
  __init__.py
  types.py                     # AgentStep, AgentMessage 类型
  registry/
    __init__.py
    hub.py                     # AgentRegistry — 注册中心
  agents/
    __init__.py
    base.py                    # BaseAgent 抽象类
    default.py                 # GeneralAgent
  subagents/
    __init__.py
    base.py                    # BaseSubAgent 抽象类
    registry.py                # SubAgentRegistry
    builtin/
      __init__.py
      recipe_master.py         # RecipeMasterSubAgent 菜谱专家
      diet_planner.py          # DietPlannerSubAgent 饮食规划(暂为骨架)
  tools/                       # 扩展现有tools
    __init__.py                 # ToolRegistry 工具注册
    base.py                    # @tool 装饰器
    recipe_tools.py            # (现有)
    user_tools.py              # (现有)
    nutrition_tools.py         # (现有)
    web_search.py              # [P2] 联网搜索
  prompts/
    __init__.py
    default.py                 # 通用提示词
    compression.py             # 压缩提示词
  models.py                    # (现有)
  router.py                    # (现有,扩展)
  service.py                   # (现有,扩展)
  session_repo.py              # (现有)
```

#### 4.2.1 AgentRegistry — 可插拔Agent注册

```
设计模式: Registry Pattern

class AgentRegistry:
    """管理中心注册所有Agent实例"""

    def register(name: str, agent_class: Type[BaseAgent], config: dict)
    def get(name: str) -> BaseAgent
    def list() -> List[str]
    def match(intent: Intent) -> BaseAgent

使用方式:
  # 在 app/agent/__init__.py 的 init_agent_module() 中注册
  registry = AgentRegistry()
  registry.register("recipe_master", RecipeMasterAgent, {...})
  registry.register("diet_planner", DietPlannerAgent, {...})
  registry.register("general", GeneralAgent, {...})
```

#### 4.2.2 BaseAgent — 统一Agent接口

```python
class BaseAgent(ABC):
    """所有Agent的基类"""

    name: str
    description: str
    intent_match: List[str]  # 匹配哪些意图

    @abstractmethod
    async def execute(
        self,
        user_message: str,
        context: AgentContext,       # 包含user_id, session, history等
        stream_callback: Callable    # SSE回调
    ) -> AgentResult:
        """执行Agent逻辑,生成响应"""
        ...

class AgentContext:
    user_id: str
    session_id: str
    intent: Intent
    rewritten_query: str | None
    compressed_history: str | None
    recent_messages: List[Message]
```

**为什么引入 Registry 而不是硬编码路由?**
- 解耦意图识别和Agent执行，新增Agent只需注册
- 后续可以配置化（config.yml 控制启用哪些Agent）
- 测试时可以用 MockAgent 替换真实Agent

### 4.3 RAG 增强

#### 4.3.1 MetadataFilter

```
作用: 在RAG检索后按元数据过滤

过滤维度:
  - difficulty: 难度等级 (1-5星)
  - category: 菜系分类 (荤菜/蔬菜/汤类/主食等)
  - cooking_time: 烹饪时间估算
  - dietary_tags: 饮食标签 (低脂/高蛋白/素食等)

实现: Milvus Scalar Filtering (基于Milvus的标量字段过滤)
      在检索时通过 expr 参数传入过滤条件

配置:
  rag:
    metadata_filter:
      enabled: true
      default_top_k: 9
      filter_fields: ["difficulty", "category", "cooking_time_minutes"]
```

#### 4.3.2 Reranker — 结果精排

```
位置: 在Retrieval之后, LLM生成之前

流程:
  检索结果(Top 9) → Reranker → 精排结果(Top 5) → LLM

实现方案:
  Phase 1: 内置 Cross-Encoder Reranker
    模型: DashScope rerank模型 (gte-rerank)
    或: 本地 ONNX 模型 (bge-reranker-base, ~100MB)
  
  Phase 2: 可扩展外部Reranker
    SiliconFlow / Cohere Rerank API

为什么需要Reranker:
  - Milvus Dense+Sparse检索是粗排,基于向量相似度
  - Reranker是精排,基于query和doc的深度语义匹配
  - 典型提升: MRR +15~25%, 用户感觉"结果更相关"

目录结构:
  app/rag/rerankers/
    __init__.py
    base.py                   # BaseReranker 抽象类
    dashscope_reranker.py     # DashScope Reranker
```

---

## 五、P2 详细设计 — 饮食规划 + Web搜索 + 评价系统

### 5.1 饮食规划模块 (`app/diet/`)

```
app/diet/
  __init__.py
  service.py               # DietService 核心业务
  database/
    __init__.py
    models.py              # DietPlan, DietLog ORM
    repository.py          # CRUD
  tools/
    __init__.py
    diet_plan_tool.py       # @tool generate_diet_plan
    diet_log_tool.py        # @tool log_meal
    diet_analysis_tool.py   # @tool analyze_nutrition
  prompts/
    __init__.py
    log_parsing.py          # 饮食日志解析提示词

API端点:
  POST   /api/v1/diet/plans          # 创建饮食计划
  GET    /api/v1/diet/plans          # 列表
  GET    /api/v1/diet/plans/{id}     # 详情
  DELETE /api/v1/diet/plans/{id}     # 删除
  POST   /api/v1/diet/logs           # 记录饮食日志
  GET    /api/v1/diet/logs           # 列表
  GET    /api/v1/diet/analysis       # 营养分析报告
```

#### 5.1.1 数据模型

```
diet_plans:
  id            UUID
  user_id       UUID FK
  goal          VARCHAR(50)     # lose_weight / gain_muscle / balanced
  days          INTEGER         # 计划天数
  content       JSONB           # 完整计划JSON
  calories_target INTEGER
  created_at    TIMESTAMP

diet_logs:
  id            UUID
  user_id       UUID FK
  meal_type     VARCHAR(20)     # breakfast / lunch / dinner / snack
  description   TEXT            # "吃了红烧肉和米饭"
  parsed_foods  JSONB           # LLM解析后的结构化数据
  calories      INTEGER         # 估算卡路里
  logged_at     DATE
```

#### 5.1.2 DietPlannerAgent 工作流

```
用户: "帮我制定一周减脂食谱"
  │
  ▼
IntentDetector → diet_plan
  │
  ▼
DietPlannerAgent.execute()
  ├─ 收集用户信息: 目标(减脂)、热量目标(1500卡/天)
  ├─ 调用 RAG 搜索低卡菜谱
  └─ LLM生成一周计划(早中晚)
     → SSE流式返回表格/列表
```

### 5.2 Web搜索工具 (P2)

```
app/agent/tools/web_search.py

@tool web_search(query: str) -> List[SearchResult]
  用途: 当本地菜谱库找不到时,联网搜索
  实现: DashScope 搜索API 或 Bing Search API
  触发: Agent 判断本地 RAG 结果不足时自动调用
```

### 5.3 评价/反馈系统 (P2)

```
app/api/v1/endpoints/evaluation.py

API端点:
  POST /api/v1/evaluations
    记录: user_id, query, response_id, rating(1-5), comment

数据用途:
  - 统计RAG效果
  - 收集用户偏好
  - 训练数据积累

数据模型:
  evaluations:
    id            UUID
    user_id       UUID FK
    query         TEXT
    response_id   UUID
    rating        SMALLINT      # 1-5
    comment       TEXT
    created_at    TIMESTAMP
```

---

## 六、数据库变更汇总

### P0 新增表
- `audit_logs` — 安全审计日志

### P0 修改表
- `conversations` 新增 `compressed_summary`, `compressed_count`
- `agent_sessions` 新增 `compressed_summary`, `compressed_count`

### P1-P2 新增表
- `diet_plans` — 饮食计划
- `diet_logs` — 饮食日志
- `evaluations` — 用户评价

---

## 七、配置变更汇总

```yaml
# config.yml 新增配置段

security:
  rate_limit:
    enabled: true
    default: "60/minute"
    endpoints:
      "/api/v1/agent/chat": "10/minute"
      "/api/v1/conversations/*/chat": "20/minute"
      "/api/v1/auth/login": "5/minute"
      "/api/v1/auth/register": "3/minute"

  prompt_guard:
    enabled: true
    max_message_length: 4000
    deny_patterns: ["prompt_injection", "jailbreak", "dos_attempt"]

context:
  compression:
    enabled: true
    trigger_turns: 10          # 超过10轮触发压缩
    incremental_turns: 5       # 后续每5轮增量压缩
    recent_keep_turns: 5       # 保留最近5轮原文
    max_tokens: 8192
    reserve_ratio: 0.6         # 使用max_tokens的60%阈值

conversation:
  intent_detection:
    enabled: true
  query_rewriting:
    enabled: true
    max_rewrite_length: 200

rag:
  reranker:
    enabled: true
    model: "gte-rerank"       # DashScope rerank
    top_n: 5                  # 精排后保留数量
  metadata_filter:
    enabled: true

diet:
  enabled: false              # P2启用
  default_calories:
    lose_weight: 1500
    gain_muscle: 2500
    balanced: 2000
```

---

## 八、实施路线图

### P0 (安全+上下文) — 预计 3-4 轮
```
第1轮: app/security/middleware/         RateLimiter + PromptGuard
第2轮: app/security/audit.py + models   审计日志
第3轮: app/context/                     上下文管理器 + Compressor
第4轮: 集成测试 + 配置文件更新
```

### P1 (意图+Agent+RAG) — 预计 5-6 轮
```
第5轮: app/conversation/intent.py       意图检测器
第6轮: app/conversation/query_rewriter.py  查询改写
第7轮: app/agent/ 重构                   AgentRegistry + BaseAgent
第8轮: app/agent/subagents/             RecipeMaster + DietPlanner
第9轮: app/rag/rerankers/               精排 + MetadataFilter
第10轮: 前端适配 + 集成测试
```

### P2 (饮食+搜索+评价) — 预计 4-5 轮
```
第11轮: app/diet/                       饮食规划核心
第12轮: app/diet/tools/                 饮食工具集
第13轮: app/agent/tools/web_search.py   联网搜索
第14轮: app/api/v1/endpoints/evaluation.py  评价系统
第15轮: 前端饮食页面 + 全面测试
```

---

## 九、设计原则

1. **渐进增强**: 每一步都是可用的增量，不破坏现有功能
2. **零停机升级**: 新模块用 Feature Flag 控制启用，出错可快速关闭
3. **复用现有基础设施**: 不引入新的数据库/中间件，PG + Redis + Milvus 足够了
4. **延迟优先**: 安全检测走热路径(规则引擎 < 1ms)，增强功能走温路径(LLM辅助 200ms+)
5. **向前兼容**: 新表新字段都用 NULLABLE 或默认值，旧数据不受影响
