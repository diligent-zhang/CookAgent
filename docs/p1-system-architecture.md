# CookAgent P1 系统架构全景

> 日期: 2026-06-26 | 状态: P1 实施完成

---

## 一、整体架构：三层流水线 + 意图路由

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              FastAPI 应用层                                  │
│                                                                             │
│  GET /                    → 健康检查                                         │
│  /api/v1/auth/*           → 认证模块 (JWT + bcrypt)                          │
│  /api/v1/conversations/*  → 旧版 RAG 对话 (兼容保留)                          │
│  /api/v1/agent/*          → P1 Agent 对话 ★ 主入口                           │
│                                                                             │
└──────────────────────────────────┬──────────────────────────────────────────┘
                                   │ POST /api/v1/agent/chat
                                   ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                          AgentService.stream_agent_chat()                    │
│                         (app/agent/service.py)                               │
│                                                                             │
│  ┌─ Step 1 ─────────────────────────────────────────────────────────────┐  │
│  │  保存用户消息到 PostgreSQL (agent_messages 表)                         │  │
│  └──────────────────────────────────────────────────────────────────────┘  │
│                                   │                                         │
│  ┌─ Step 2 ─────────────────────────────────────────────────────────────┐  │
│  │  IntentDetector.detect(user_message)           ~200ms (fast LLM)      │  │
│  │                                                                       │  │
│  │  "帮我找几个快手的减脂菜"                                              │  │
│  │       ↓ fast LLM (qwen-plus) 分类                                     │  │
│  │  Intent(type="recipe_search", confidence=0.95,                        │  │
│  │         keywords=["快手","减脂"],                                      │  │
│  │         filters={difficulty:2, tags:["低脂"]})                        │  │
│  │                                                                       │  │
│  │  缓存: Redis key=intent:{md5(query)}, TTL=3600s                       │  │
│  │  降级: LLM超时/出错 → fallback="recipe_search"                        │  │
│  └──────────────────────────────────────────────────────────────────────┘  │
│                                   │                                         │
│  ┌─ Step 3 ─────────────────────────────────────────────────────────────┐  │
│  │  QueryRewriter.rewrite(query, intent_type)      ~200ms (fast LLM)     │  │
│  │                                                                       │  │
│  │  仅 recipe_search / cooking_help 意图触发                              │  │
│  │                                                                       │  │
│  │  "帮我找几个快手的减脂菜"                                              │  │
│  │       ↓ fast LLM 改写                                                 │  │
│  │  "快手 减脂 菜谱 低脂 30分钟内"                                        │  │
│  │                                                                       │  │
│  │  降级: LLM出错 → 返回原查询, 不阻断主流程                              │  │
│  └──────────────────────────────────────────────────────────────────────┘  │
│                                   │                                         │
│  ┌─ Step 4 ─────────────────────────────────────────────────────────────┐  │
│  │  构建 AgentContext (统一上下文对象)                                    │  │
│  │                                                                       │  │
│  │  AgentContext(                                                        │  │
│  │    user_id=...,  session_id=...,                                      │  │
│  │    intent_type="recipe_search",                                       │  │
│  │    original_query="帮我找几个快手的减脂菜",                             │  │
│  │    rewritten_query="快手 减脂 菜谱 低脂 30分钟内",                      │  │
│  │    recent_messages=[...最近20条...],                                   │  │
│  │    compressed_summary=None  # P2 接上下文压缩                           │  │
│  │  )                                                                    │  │
│  └──────────────────────────────────────────────────────────────────────┘  │
│                                   │                                         │
│  ┌─ Step 5 ─────────────────────────────────────────────────────────────┐  │
│  │  AgentRegistry.match(intent_type)                                     │  │
│  │                                                                       │  │
│  │  路由表 (启动时注册, 全局单例):                                        │  │
│  │    recipe_search   → RecipeMasterAgent (ReAct + 6工具 + Reranker)     │  │
│  │    cooking_help    → GeneralAgent      (ReAct + 6工具)                 │  │
│  │    ingredient_ask  → GeneralAgent      (ReAct + 6工具)                 │  │
│  │    general_chat    → GeneralAgent      (ReAct + 6工具)                 │  │
│  │    diet_plan       → DietPlannerAgent  (P2骨架, 返回"即将上线")       │  │
│  │    未匹配          → GeneralAgent      (default 兜底)                 │  │
│  └──────────────────────────────────────────────────────────────────────┘  │
│                                   │                                         │
│  ┌─ Step 6 ─────────────────────────────────────────────────────────────┐  │
│  │  agent.execute(context, stream_callback)                              │  │
│  │                                                                       │  │
│  │  Agent 内部通过 stream_callback 推送 SSE 事件:                         │  │
│  │    thinking  → "正在精准搜索菜谱..."                                   │  │
│  │    tool_call → {name:"search_recipes", args:{query:"快手 减脂..."}}   │  │
│  │    observation → 工具返回结果                                          │  │
│  │    token     → "推" "荐" "以" "下" ... (逐token流式)                   │  │
│  │    sources   → [{dish_name, category, relevance_score}, ...]           │  │
│  └──────────────────────────────────────────────────────────────────────┘  │
│                                   │                                         │
│  ┌─ Step 7 ─────────────────────────────────────────────────────────────┐  │
│  │  保存 assistant 回答 + 自动生成会话标题                                │  │
│  └──────────────────────────────────────────────────────────────────────┘  │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 二、Agent 注册与路由机制

```
应用启动 (main.py lifespan)
│
├─ LLMProvider()          → 管理 qwen-plus(快) / qwen-max(标准) 两层模型
├─ RAGService(redis)      → 管理 Embedding + Milvus + 缓存
├─ init_conversation_module(llm, rag)
│   └─ 注入到旧版对话系统
│
└─ init_agent_module(llm, rag, redis)        ← app/agent/router.py
    │
    ├─ 创建 DashScopeReranker (如果 config.rag.reranker.enabled)
    │
    ├─ 创建 Agent 实例:
    │   RecipeMasterAgent(llm, rag, reranker)
    │   GeneralAgent(llm, rag)
    │   DietPlannerAgent(llm, rag)
    │
    └─ 注册到 AgentRegistry (全局单例):
        registry.register(recipe_master)   → intent路由: recipe_search
        registry.register(general)         → intent路由: cooking_help, ingredient_ask, general_chat
        registry.set_default("general")    → 兜底


每次请求 (POST /api/v1/agent/chat):
│
├─ FastAPI 依赖注入 → AgentService(db, _llm_provider, _rag_service)
│
└─ service.stream_agent_chat()
    └─ registry = get_agent_registry()     ← 取全局单例
    └─ agent = registry.match(intent.type) ← 按意图路由
    └─ agent.execute(context, callback)    ← 执行选中的 Agent
```

---

## 三、ReAct 循环详解

```
RecipeMasterAgent.execute(context, callback)
│
├─ 从 context 取 rewritten_query (改写后) 或 original_query (原始)
├─ 构建 LangChain 格式历史消息 (user/assistant 角色, 排除中间推理步骤)
├─ 创建 LLMInvoker("normal") → qwen-max, 支持 tool calling
├─ 创建 ReActAgent(invoker, tools=6个工具, max_iterations=8)
│
└─ ReActAgent.run(user_message, user_id, context_messages)
    │
    │  消息初始化:
    │  messages = [
    │    SystemMessage(RECIPE_MASTER_PROMPT),  ← 菜谱专家专用提示词
    │    ...历史消息...,
    │    HumanMessage("快手 减脂 菜谱 低脂 30分钟内")
    │  ]
    │
    └─ for i in range(8):  ← ReAct 循环, 最多8轮
        │
        ├─ Step A: LLM 推理 (带 tool schemas)
        │   invoker.ainvoke_with_tools(messages, tools)
        │   → LLM 返回: {tool_calls: [{name:"search_recipes", args:{query:"..."}}]}
        │               或: {content: "推荐以下几道菜..."}  ← 最终回答
        │
        ├─ Step B: 判断分支
        │
        │   ┌── 有 tool_calls? ──────────────────────────────────────┐
        │   │                                                         │
        │   │  1. yield {"type":"tool_call", "tool_name":"search_recipes", ...}
        │   │                                                         │
        │   │  2. 执行工具:                                            │
        │   │     tool_func = self._tool_map["search_recipes"]        │
        │   │     observation = await tool_func.ainvoke(args)         │
        │   │                                                         │
        │   │     工具内部流程 (search_recipes):                       │
        │   │       RAGService.retrieve(query, top_k=3, expr=...)    │
        │   │         ├─ 缓存检查: L1 Redis → L2 Milvus语义缓存       │
        │   │         ├─ 混合检索: Milvus Dense(向量) + Sparse(BM25)  │
        │   │         ├─ 智能排序: 关键词查询偏向BM25, 语义查询偏向Dense│
        │   │         ├─ 分数过滤: 低于 threshold 的丢弃              │
        │   │         └─ 文档处理: chunk → 父文档还原                 │
        │   │       → 返回: [Document("清蒸鲈鱼..."), ...]            │
        │   │                                                         │
        │   │  3. yield {"type":"observation", "content": 工具结果}    │
        │   │                                                         │
        │   │  4. messages.append(AI的tool_call消息)                   │
        │   │     messages.append(ToolMessage(observation))            │
        │   │                                                         │
        │   │  5. continue → 下一轮迭代 (LLM 看到新信息后继续推理)     │
        │   │                                                         │
        │   └─────────────────────────────────────────────────────────┘
        │
        └── 无 tool_calls? ───────────────────────────────────────────┐
            │                                                         │
            │  LLM 给出了最终回答                                       │
            │                                                         │
            │  yield {"type":"thought", "content":"正在组织回答..."}    │
            │                                                         │
            │  流式输出逐 token:                                        │
            │    async for chunk in invoker.astream(messages):         │
            │      yield {"type":"token", "content":"推"}              │
            │      yield {"type":"token", "content":"荐"}              │
            │      ...                                                 │
            │                                                         │
            │  yield {"type":"done", "content": 完整回答}               │
            │  return  ← 结束循环                                      │
            │                                                         │
            └─────────────────────────────────────────────────────────┘
```

---

## 四、6 个工具详解

```
工具注册 (app/agent/tools/__init__.py: get_all_tools())
│
├─ search_recipes(query, category?, difficulty?, max_results=3)
│   ├─ 来源: app/agent/tools/recipe_tools.py
│   ├─ 类型: async, LangChain @tool 装饰器
│   ├─ 内部调用: RAGService.retrieve(query, top_k, expr)
│   │   └─ Milvus 混合检索 (Dense向量 + BM25稀疏)
│   └─ 返回: 菜谱列表字符串 (菜名 + 分类 + 难度 + 内容摘要500字)
│
├─ get_recipe_detail(dish_name)
│   ├─ 来源: app/agent/tools/recipe_tools.py
│   ├─ 内部调用: RAGService.retrieve(dish_name, top_k=1)
│   └─ 返回: 完整菜谱文本 (分类 + 难度 + 做法步骤)
│
├─ get_user_dietary_info(user_id)
│   ├─ 来源: app/agent/tools/user_tools.py
│   ├─ 数据库查询: User 表的 dietary_preferences 字段
│   └─ 返回: 饮食偏好 JSON (过敏食材 + 饮食类型 + 卡路里目标)
│
├─ check_ingredient_safety(dish_name, user_id)
│   ├─ 来源: app/agent/tools/user_tools.py
│   ├─ 逻辑: 查菜谱食材 → 对比用户过敏列表 → 标记冲突
│   └─ 返回: 安全/警告 + 冲突食材列表
│
├─ estimate_calories(dish_name)
│   ├─ 来源: app/agent/tools/nutrition_tools.py
│   ├─ 逻辑: RAG检索菜谱 → 提取食材和用量 → LLM估算
│   └─ 返回: 估算热量范围 (如 "约 350-450 千卡")
│
└─ suggest_substitutes(ingredient, dietary_restriction?)
    ├─ 来源: app/agent/tools/nutrition_tools.py
    ├─ 逻辑: LLM根据限制条件推荐替代食材
    └─ 返回: 替代建议列表 + 替代后的影响说明
```

---

## 五、RAG 检索管道

```
RAGService.retrieve(query, user_id, top_k=9, expr=None)
│
├─ Step 1: L1 缓存检查 (Redis 精确匹配)
│   key = md5(query)
│   命中 → 直接返回, 延迟 <5ms
│
├─ Step 2: L2 缓存检查 (Milvus 语义缓存)
│   向量相似度 >= 0.92 → 命中
│   延迟 ~50ms
│
├─ Step 3: 混合检索 (未命中才执行)
│   │
│   ├─ Milvus Hybrid Search API
│   │   ├─ Dense 路径: BGE Embedding 向量 → cosine 相似度
│   │   └─ Sparse 路径: BM25 关键词 → 精确匹配打分
│   │
│   ├─ 智能融合策略 (intelligent_ranker_selection):
│   │   ├─ 查询含"怎么做/步骤" → BM25权重0.6 (精确匹配优先)
│   │   ├─ 查询含"推荐/有什么" → Dense权重0.6 (语义匹配优先)
│   │   └─ 其他 → 均衡权重 0.5:0.5
│   │
│   ├─ 融合公式 (Weighted模式):
│   │   final_score = w1 × norm(dense_score) + w2 × norm(sparse_score)
│   │
│   ├─ 分数阈值过滤: score < 0.2 → 丢弃
│   │
│   └─ 返回 top_k=9 个候选文档
│
├─ Step 4: 可选的 Reranker 精排 (P1新增)
│   ├─ 条件: config.rag.reranker.enabled=true
│   └─ 调用 DashScope gte-rerank API
│       9个候选 → Cross-Encoder重打分 → 取top_n=5
│       延迟 ~300ms
│       降级: API超时 → 返回粗排前5个
│
├─ Step 5: Small-to-Large 父文档还原
│   chunk(512字) → 还原为完整菜谱文档
│
└─ Step 6: 写入双层缓存
    ├─ L1 Redis: key=md5(query), TTL=3600s
    └─ L2 Milvus: 向量 + 查询embedding, 相似度>=0.92时命中
```

---

## 六、SSE 事件流协议

前端 EventSource 接收的事件序列:

```
event: session
data: {"session_id": "sess_abc123"}
│
event: thought
data: {"type":"thought", "content":"正在理解你的需求..."}
│
event: intent
data: {"type":"intent", "intent_type":"recipe_search", "confidence":0.95}
│
event: thought
data: {"type":"thought", "content":"优化查询: 快手 减脂 菜谱 低脂 30分钟内"}
│
event: tool_call
data: {"type":"tool_call", "tool_name":"search_recipes", "arguments":{"query":"快手 减脂 菜谱 低脂 30分钟内","max_results":3}}
│
event: observation
data: {"type":"observation", "tool_name":"search_recipes", "content":"找到 3 个相关菜谱：\n--- 1. 清蒸鲈鱼 ..."}
│
event: tool_call
data: {"type":"tool_call", "tool_name":"get_user_dietary_info", "arguments":{"user_id":"u123"}}
│
event: observation
data: {"type":"observation", "tool_name":"get_user_dietary_info", "content":"用户偏好：低脂..."}
│
event: thought
data: {"type":"thought", "content":"正在组织回答..."}
│
event: token
data: {"type":"token", "content":"推"}
event: token
data: {"type":"token", "content":"荐"}
event: token
data: {"type":"token", "content":"以"}
... (逐token)
│
event: sources
data: {"type":"sources", "sources":[{"dish_name":"清蒸鲈鱼","category":"粤菜","difficulty":"简单","relevance_score":0.92}]}
│
event: done
data: {"type":"done", "content":"推荐以下几道快手减脂菜：\n1. 清蒸鲈鱼..."}
```

---

## 七、数据库与基础设施

```
┌──────────────────────────────────────────────────────────────┐
│                      Docker 基础设施                          │
│                                                              │
│  cookhero_postgres  :5432  ← 业务数据 (用户/会话/消息/日志)    │
│  cookhero_redis     :6379  ← L1缓存 + 速率限制 + 意图缓存     │
│  cookhero_milvus    :19530 ← 向量检索 + L2语义缓存 + BM25     │
│                                                              │
└──────────────────────────────────────────────────────────────┘

PostgreSQL 核心表:
├─ users              ← 用户 (JWT认证)
├─ conversations      ← 旧版对话 (兼容)
├─ messages           ← 旧版消息 (兼容)
├─ agent_sessions     ← P1 Agent 会话
├─ agent_messages     ← P1 Agent 消息 (含 tool_call/observation)
├─ recipe_documents   ← 菜谱文档索引
├─ llm_usage_logs     ← LLM 用量追踪 (token消耗+延迟)
└─ evaluations        ← 用户评价 (P2)

Milvus Collections:
├─ cook_hero_recipes  ← 全局菜谱 (Dense+Sparse双索引)
└─ cook_hero_personal_docs ← 用户个人文档
```

---

## 八、配置驱动策略

```
config.yml 配置项 → Settings 属性 → 运行时行为

conversation.intent_detection.enabled       → 意图检测开关
conversation.intent_detection.cache_ttl     → 意图缓存时间
conversation.intent_detection.fallback      → LLM超时/出错时的默认意图类型
conversation.query_rewriting.enabled         → 查询改写开关
conversation.query_rewriting.max_length      → 改写后最大长度

agent.enabled_agents                        → 启动时注册哪些 Agent
                                             ["recipe_master","general"] 当前
                                             加 "diet_planner" 即启用P2

rag.reranker.enabled                        → Reranker精排开关
rag.reranker.model                          → 精排模型 (gte-rerank)
rag.reranker.top_n                          → 精排后保留数量 (5)
rag.metadata_filter.enabled                 → Milvus标量过滤开关

retrieval.top_k                             → 粗排候选数 (9)
retrieval.score_threshold                   → 最低分数阈值 (0.2)
retrieval.ranker_type                       → 融合方式 (weighted/rrf)
```

---

## 九、关键文件索引

```
app/
├── main.py                    ← 应用入口, 生命周期管理, 路由注册
├── config/
│   ├── config_loader.py       ← YAML + .env 加载
│   └── config.py              ← Settings 单例, 类型化配置访问
├── agent/
│   ├── agent.py               ← ReActAgent 核心循环 ★
│   ├── router.py              ← API路由 + init_agent_module(注册式)
│   ├── service.py             ← AgentService (意图路由编排) ★
│   ├── session_repo.py        ← Agent 会话/消息 CRUD
│   ├── agents/
│   │   ├── base.py            ← BaseAgent, AgentContext, AgentResult ★
│   │   └── default.py         ← GeneralAgent (通用对话)
│   ├── registry/
│   │   └── hub.py             ← AgentRegistry (注册中心) ★
│   ├── subagents/builtin/
│   │   ├── recipe_master.py   ← RecipeMasterAgent (菜谱专家) ★
│   │   └── diet_planner.py    ← DietPlannerAgent (P2骨架)
│   └── tools/
│       ├── recipe_tools.py    ← search_recipes, get_recipe_detail
│       ├── user_tools.py      ← get_user_dietary_info, check_ingredient_safety
│       └── nutrition_tools.py ← estimate_calories, suggest_substitutes
├── conversation/
│   ├── prompts.py             ← 意图分类 + 查询改写 LLM 提示词
│   ├── intent.py              ← IntentDetector ★
│   ├── query_rewriter.py      ← QueryRewriter ★
│   └── service.py             ← ConversationService (旧版, 已集成P1)
├── rag/
│   ├── service.py             ← RAGService (检索入口)
│   ├── pipeline/
│   │   └── retrieval.py       ← 混合检索 + build_metadata_filter_expr ★
│   └── rerankers/
│       ├── base.py            ← BaseReranker 抽象类
│       └── dashscope_reranker.py ← DashScope 精排实现
├── llm/
│   ├── provider.py            ← LLMProvider, LLMInvoker (两层模型)
│   └── callbacks.py           ← Token 用量追踪回调
└── auth/                      ← JWT 认证 (router/security/service)
```

---

## 十、P0 与 P1 核心变化对比

| | P0 (之前) | P1 (现在) |
|---|---|---|
| 请求入口 | 固定走 ReActAgent | 意图识别 → 路由到对应 Agent |
| 查询处理 | 直接用用户原文检索 | QueryRewriter 改写后再检索 |
| Agent 创建 | 每次请求硬编码 new ReActAgent | 启动时注册，请求时 match() |
| 新增 Agent | 改 service.py 代码 | registry.register() 一行注册 |
| 检索精排 | 无 | Reranker (gte-rerank) 9→5 |
| 元数据过滤 | 无 | Milvus expr 标量过滤 |
| 降级机制 | 无 | 意图失败→默认意图, 改写失败→原查询 |
| 扩展性 | 低 (硬编码) | 高 (注册模式 + 提供者模式) |
