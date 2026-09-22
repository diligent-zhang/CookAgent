# CookAgent（CookHero）项目功能与架构指南

> 本文面向两类读者：
> 1. 想快速理解这个项目「能做什么、一次请求怎么流动」的产品/工程同学；
> 2. **有 Java 背景、想用 MVC / Spring Boot 的心智模型来读懂这个 Python 项目**的开发者。
>
> **重要说明（文档口径）**：本文所有结论都来自通读仓库源码，路径与函数名均经过核对。
> 仓库中存在一部分「已实现但尚未接入主链路」的组件（例如安全防护、上下文压缩），
> 文中会**明确标注**，不会把它们描述成已生效的功能。详见 [1.9 节](#19-组件落地状态核对不要被注释误导)。

---

## 第一部分 项目功能、逻辑流程与数据链路

### 1.1 项目定位

CookAgent（内部品牌名 **CookHero**，见 `app/config/config.py` 中 `Settings.PROJECT_NAME = "CookHero"`）是一个
**AI 智能烹饪 / 饮食助手**：

- 用户用自然语言问「番茄炒蛋怎么做」「我鸡蛋过敏，推荐几个清淡的晚餐」，系统给出**基于菜谱库检索**的步骤化回答；
- 后端是一个**多智能体（Multi-Agent）**系统：根据用户意图，把请求路由到不同的子智能体；
- 同时具备**饮食记录 / 饮食计划 / 营养分析**的持久化能力，以及 **LLM 用量统计**、**用户认证**等配套模块。

技术栈一句话概括：

| 层 | 技术 |
|---|---|
| 后端 | FastAPI + uvicorn（Python），全异步 `async/await` |
| LLM 编排 | LangChain（`langchain-core` 1.x、`langchain-openai`、`langchain-milvus`） |
| 模型 | 阿里云 DashScope（OpenAI 兼容模式）：`qwen-plus`（fast 层）/ `qwen-max`（normal 层）；`text-embedding-v3` 向量化；`gte-rerank` 精排 |
| 向量库 | Milvus（19530） |
| 关系库 | PostgreSQL（宿主映射端口 **5433**，因为 5432 被本机 PostgreSQL 17 服务占用） |
| 缓存 | Redis（6379），用于 L1 缓存、意图缓存、限流 |
| 前端 | Vue 3 + Vite + TypeScript + Tailwind CSS 4 + vue-router，端口 5173 |

### 1.2 功能模块清单（对照代码）

| 功能 | 关键代码位置 | 说明 |
|---|---|---|
| RAG 菜谱检索问答 | `app/rag/service.py` → `RAGService.retrieve()` | 混合检索 + 精排 + 双层缓存的统一入口 |
| 多轮对话（RAG 版） | `app/conversation/service.py` → `ConversationService.stream_chat()` | 保存消息 → 检索 → 拼 Prompt → 流式生成 |
| Agent 与子智能体 | `app/agent/`、`app/agent/subagents/builtin/`、`app/agent/agents/default.py` | 三个 Agent：`recipe_master` / `general` / `diet_planner` |
| Agent 注册与路由 | `app/agent/registry/hub.py` → `AgentRegistry` | 意图类型 → Agent 实例的注册表，支持 default 兜底 |
| ReAct 循环（推理+工具调用） | `app/agent/agent.py` → `ReActAgent.run()` | 最多 8 轮 Thought→Action→Observation，流式输出思考过程 |
| 工具集（16 个） | `app/agent/tools/__init__.py` → `get_all_tools()` | 菜谱检索、菜谱详情、过敏检查、热量估算、食材替换、周计划、导出 ICS/HTML、饮食记录、Web 搜索、计算器等 |
| 意图识别 | `app/conversation/intent.py` → `IntentDetector` | fast LLM 分类到 5 种意图，支持 Redis 缓存与降级 |
| 查询改写 | `app/conversation/query_rewriter.py` → `QueryRewriter` | 仅对 `recipe_search` / `cooking_help` 意图触发 |
| 混合检索（Dense + Sparse） | `app/rag/pipeline/retrieval.py` → `RetrievalOptimizationModule.hybrid_search()` | Milvus 向量 + BM25 加权/ RRF 融合，带智能权重选择 |
| 检索精排（Reranker） | `app/rag/rerankers/dashscope_reranker.py` → `DashScopeReranker` | `gte-rerank` Cross-Encoder 精排，通过 `RAGService.set_reranker()` 注入 |
| 双层缓存 | `app/rag/cache/cache_manager.py` → `CacheManager` | L1 Redis 精确匹配 + L2 Milvus 语义缓存（阈值 0.92） |
| Small-to-Large 检索 | `app/rag/pipeline/document_processor.py` → `DocumentProcessor.post_process_retrieval()` | 命中 chunk 后按 `parent_id` 回表取完整父文档并去重 |
| 数据摄取 | `app/ingestion/pipeline.py`、`parser.py`、`document_repo.py` | 菜谱 Markdown → 切分 → 入 Milvus + PostgreSQL |
| 饮食记录 / 计划 / 分析 | `app/diet/`（`router.py` / `service.py` / `repository.py` / `models.py`） | 计划餐次、饮食记录、日/周汇总、计划 vs 实际偏差、用户偏好 |
| 饮食计划导出 | `app/agent/tools/meal_plan_tools.py` | Markdown / ICS（日历）/ HTML 三种格式，含文件名白名单防路径穿越 |
| LLM 用量统计 | `app/llm/callbacks.py`、`app/agent/llm_stats_router.py` | 回调自动记录 token 与耗时到 `llm_usage_logs` 表 |
| 用户认证 | `app/auth/` | JWT（HS256，默认 24 小时）+ bcrypt 密码哈希 |
| 限流 / Prompt 防护 | `app/security/`（`middleware/rate_limiter.py`、`middleware/prompt_guard.py`、`guard.py`） | **已实现但未接入路由**，见 1.9 节 |
| 上下文压缩 | `app/context/manager.py`、`app/context/compress.py` | **已实现但未被 Service 调用**，见 1.9 节 |
| 配置系统 | `config.yml` + `.env` + `app/config/config_loader.py` + `config.py` | `${ENV_VAR}` 占位符 + 类型化 `settings` 单例 |

### 1.3 一次问答的完整逻辑流程（Agent 主链路）

前端路由 `frontend/src/router/index.ts` 中，`/` 的兜底规则是 `redirect: '/agent'`，
所以**默认走的就是 Agent 链路**（`/api/v1/agent/chat`）。下面逐步展开。

#### Step 0 — 浏览器输入

用户在 `frontend/src/pages/ChatPage.vue` 中，由 `ChatInput.vue` 组件触发 `handleSend(content)`。
`ChatPage.vue` 根据 `route.meta.mode` 判断当前是 `chat`（RAG 对话）还是 `agent` 模式，分别调用：

```ts
// frontend/src/pages/ChatPage.vue
const { sendMessage: sendAgentMessage } = useAgent()      // agent 模式
const { sendMessage: sendChatMessage } = useConversation() // chat 模式
```

#### Step 1 — 前端状态与请求封装

`frontend/src/composables/useAgent.ts` 的 `sendMessage(content)`：

1. 立即把用户消息（临时 ID `temp-<ts>`）推入 `messages`，实现「乐观渲染」；
2. 置 `isStreaming = true`，清空 `streamingContent` 与 `currentSteps`；
3. 创建 `AbortController`（对应 UI 上的「停止生成」按钮）；
4. `for await (... of streamAgentChat(...))` 消费 SSE 事件流。

请求封装在 `frontend/src/api/agent.ts` 的 `streamAgentChat()`：它**没有**用 `client.ts` 的 `request()`
封装（因为那是普通 JSON 请求），而是直接 `fetch`，用 `res.body.getReader()` + `TextDecoder`
手写 SSE 解析：按 `\n` 切行、只取 `data: ` 开头的行、`JSON.parse` 后 yield。

> 关键点：前端**不使用** `EventSource`（它只支持 GET），而是用 `fetch` + 流式 `ReadableStream`，
> 这样才能发 POST 并携带 `Authorization: Bearer <token>` 头。

#### Step 2 — Vite 反向代理

`frontend/vite.config.ts` 配置了代理，把 `/api` 转发到后端：

```ts
server: { proxy: { '/api': { target: 'http://localhost:8000', changeOrigin: true } } }
```

API 前缀统一为 `/api/v1`（`frontend/src/api/client.ts` 中 `const API_BASE = '/api/v1'`）。

#### Step 3 — FastAPI 入口与路由

`app/main.py` 中 `app = FastAPI(...)`，通过 `app.include_router(agent_router, prefix=settings.API_V1_STR)`
挂载。`app/agent/router.py` 里 `router = APIRouter(prefix="/agent", tags=["Agent"])`，
最终路径即 `POST /api/v1/agent/chat`。

`agent_chat()` 先处理会话：有 `body.session_id` 就校验归属，否则 `service.create_session()` 新建，
然后返回 `StreamingResponse(event_stream(), media_type="text/event-stream")`。

#### Step 4 — 认证依赖注入

`current_user: User = Depends(get_current_user)`（`app/auth/dependencies.py`）：
`HTTPBearer` 解析 `Authorization` 头 → `decode_access_token()`（HS256）→ 查库确认用户存在 → 返回 `User` ORM 对象。
任何一步失败抛 401。

#### Step 5 — 依赖注入出 Service

```python
def get_agent_service(db: AsyncSession = Depends(get_db)) -> AgentService:
    return AgentService(db, _llm_provider, _rag_service, _redis_client)
```

注意 `_llm_provider` / `_rag_service` / `_redis_client` 是**模块级全局变量**，
在 `init_agent_module()` 中被注入（见 [2.2 节 DI 对照](#22-java--python-对照表)）。

#### Step 6 — Service 编排：意图识别 → 查询改写 → 路由

`app/agent/service.py` 的 `AgentService.stream_agent_chat()`：

1. **先加载历史**（`get_last_n_messages(session_id, 20)`）**再保存当前消息**——
   顺序很重要，否则当前问题会在「历史」和「显式追加」中出现两次；
2. 保存用户消息（`repo.add_message(role="user")`）；
3. `IntentDetector.detect(user_message)` → 得到 `Intent(type, confidence, keywords, filters)`，
   `yield {"type": "intent", ...}` 给前端；
4. `QueryRewriter.rewrite(query, intent_type)` → 得到 `rewritten_query`（仅特定意图触发）；
5. 构建 `AgentContext`（`app/agent/agents/base.py`）；
6. `get_agent_registry().match(intent.type)` → 得到具体 Agent 实例。

#### Step 7 — 意图路由表

`app/agent/router.py` 的 `init_agent_module()` 在启动时创建三个 Agent 并注册：

| 意图类型 | 路由到 | 类 |
|---|---|---|
| `recipe_search` | `recipe_master` | `app/agent/subagents/builtin/recipe_master.py` → `RecipeMasterAgent` |
| `general_chat` / `cooking_help` / `ingredient_ask` | `general` | `app/agent/agents/default.py` → `GeneralAgent` |
| `diet_plan` | `diet_planner` | `app/agent/subagents/builtin/diet_planner.py` → `DietPlannerAgent` |
| 其他未匹配 | `general`（`registry.set_default("general")`） | 兜底 |

#### Step 8 — Agent 执行

**`RecipeMasterAgent`（意图 `recipe_search`）** 走的是 **ReAct 循环**（`app/agent/agent.py` 的 `ReActAgent.run()`）：

```
构建 messages = [SystemMessage(RECIPE_MASTER_PROMPT)] + 历史 + [HumanMessage(rewritten_query)]
for iteration in range(max_iterations=8):
    tool_calls, full_response, ai_message = await self._astream_and_detect_tools(messages, on_thought)
    if tool_calls:
        messages.append(ai_message)          # 只追加一次
        for tc in tool_calls:
            yield {"type": "tool_call", "tool_name": ..., "arguments": ...}
            observation = await tool_func.ainvoke(tool_args)   # 自动注入真实 user_id
            yield {"type": "observation", "tool_name": ..., "content": ...}
            messages.append(ToolMessage(...))
    else:
        yield {"type": "done", "content": full_response}
        return
```

`_astream_and_detect_tools()` 用 `astream_with_tools()` 流式调用 LLM，边生成边累积
`chunk.tool_call_chunks`（按 `index` 分组拼接 `name`/`args`，因为 JSON 参数会跨 chunk 到达），
流式结束后重建 `AIMessage` 追加进历史。

**`GeneralAgent`（轻量意图）** 不走 ReAct，直接「RAG 检索 → 拼消息 → 流式生成」：

```
yield thinking → rag_service.retrieve(query) → yield sources → invoker.astream(messages) → yield token*
```

#### Step 9 — RAG 检索内部（`app/rag/service.py` → `RAGService.retrieve()`）

这是整个系统最核心的一段，顺序为：

1. **L1 缓存**（Redis）：`CacheManager.get(query, user_id)` → key 为 `cookhero:cache:{user_id}:{md5(query)}`，命中直接返回；
2. **L2 语义缓存**（Milvus）：把 query 向量化，在 `cookhero_retrieval_cache` collection 中找最相似的历史查询，
   相似度 `>= 0.92` 即命中；
3. **智能 ranker 选择**：`retrieval.intelligent_ranker_selection(query)`
   —— 含「怎么做/如何/步骤」→ 关键词型，BM25 权重 0.6；含「推荐/适合/有哪些」→ 语义型，Dense 权重 0.6；否则 0.5:0.5；
4. **混合检索**：`retrieval.hybrid_search(...)`，内部 `asyncio.to_thread` 调 LangChain Milvus 的
   `similarity_search_with_score(query, k=top_k, fetch_k=top_k*4, ranker_type, ranker_params={"norm_score": True, ...})`，
   随后按 `score_threshold`（默认 0.2，仅 weighted 模式）过滤；
5. **分数回填**：把分数写回 `doc.metadata["retrieval_score"]`；
6. **Small-to-Large**：`processor.post_process_retrieval(chunks)` 按 `parent_id` 分组取最高分，
   回 PostgreSQL 的 `recipe_documents` 表取完整父文档，去重排序；
7. **精排**：若 `self.reranker` 非空，`await self.reranker.rerank(query, documents, top_n=top_k)`
   —— 失败只打 warning，降级用粗排结果；
8. **写缓存**：`cache.set(query, final_docs, user_id)` —— 缓存的是**精排后**的结果。

#### Step 10 — 流式回传

Agent 的事件通过 `asyncio.Queue` 生产-消费（见 1.6 节），最终在 `app/agent/router.py` 中格式化为 SSE：

```python
yield f"event: {event_type}\ndata: {json.dumps(event, ensure_ascii=False, cls=_JSONEncoder)}\n\n"
```

#### Step 11 — 落库

流式输出结束后（不是之前！），`AgentService` 才把完整回答写入 `agent_messages`（`role="assistant"`），
并用用户消息前 20 字更新会话标题（`update_session_title`）。

### 1.4 端到端数据链路图

```
 ┌──────────────────────────── 浏览器 (Vue 3 SPA, :5173) ────────────────────────────┐
 │  ChatPage.vue                                                                      │
 │    ├─ 路由 meta.mode = 'agent' | 'chat'                                            │
 │    └─ ChatInput.vue ──emit send──► useAgent.ts / useConversation.ts                │
 │                                     │                                              │
 │    AgentChatWindow.vue ◄──refs──────┤  messages / streamingContent / currentSteps   │
 │      ├─ 思考过程区块 (thought/tool_call/observation)                                │
 │      └─ Markdown 渲染 (marked)                                                      │
 └─────────────────────────────────────┬──────────────────────────────────────────────┘
                                       │ fetch(POST /api/v1/agent/chat,
                                       │       Authorization: Bearer <JWT>,
                                       │       body: {content, session_id?})
                                       │  ◄──── SSE: text/event-stream
                                       │        event: session|thought|intent|tool_call|
                                       │               observation|sources|token|done|error
                                       ▼
 ┌────────────────── Vite Dev Server (:5173) ── proxy '/api' ──► http://localhost:8000 ─┐
 └─────────────────────────────────────┬───────────────────────────────────────────────┘
                                       ▼
 ┌═════════════════════ FastAPI / uvicorn (:8000) ══════════════════════════════════════┐
 │  app/main.py : app = FastAPI(lifespan=lifespan)                                     │
 │    └─ CORSMiddleware (allow_origins: localhost:5173 / :3000)                        │
 │                                                                                     │
 │  [启动] lifespan: init_db() → init_rate_limiter() → rag_service.initialize()        │
 │          → init_conversation_module() → init_agent_module() → init_diet_module()    │
 │                                                                                     │
 │  [请求] APIRouter  (app/agent/router.py, prefix="/agent")                            │
 │    └─ Depends(get_current_user)  → JWT 解码 + 查 users 表                           │
 │    └─ Depends(get_agent_service) → AgentService(db, LLMProvider, RAGService, Redis) │
 │                                                                                     │
 │  ┌─ AgentService.stream_agent_chat() ────────────────────────────────────────────┐  │
 │  │  1. load history ──────────────► PostgreSQL: agent_messages                   │  │
 │  │  2. save user msg ────────────► PostgreSQL: agent_messages                   │  │
 │  │  3. IntentDetector.detect() ──► Redis (intent:{md5}) ──miss──► fast LLM       │  │
 │  │  4. QueryRewriter.rewrite() ──► fast LLM                                      │  │
 │  │  5. AgentRegistry.match(intent) ──► RecipeMaster / General / DietPlanner      │  │
 │  │  6. agent.execute(context, stream_callback)  [asyncio.create_task]            │  │
 │  │       └─事件──► asyncio.Queue ──► 主循环消费 ──► yield SSE                     │  │
 │  │  7. save assistant msg ───────► PostgreSQL: agent_messages                    │  │
 │  └───────────────────────────────────────────────────────────────────────────────┘  │
 │                                                                                     │
 │  ┌─ RAGService.retrieve(query, user_id) ─────────────────────────────────────────┐  │
 │  │  CacheManager.get()                                                           │  │
 │  │    ├─ L1: Redis GET cookhero:cache:{uid}:{md5} ──► hit? return (≈1ms)         │  │
 │  │    └─ L2: Milvus similarity_search_with_score(k=1) ≥0.92 ──► hit? return(≈60ms)│  │
 │  │  intelligent_ranker_selection()  → (ranker_type, weights)                     │  │
 │  │  hybrid_search() ──asyncio.to_thread──► Milvus  (Dense + Sparse 融合)          │  │
 │  │       └─ collection: cook_hero_recipes     (Dense: text-embedding-v3 向量)     │  │
 │  │  post_process_retrieval() ──► PostgreSQL: recipe_documents  (parent_id 回表)   │  │
 │  │  DashScopeReranker.rerank() ──HTTPS──► dashscope gte-rerank                    │  │
 │  │  CacheManager.set() ──► Redis L1 + Milvus L2                                  │  │
 │  └───────────────────────────────────────────────────────────────────────────────┘  │
 │                                                                                     │
 │  ┌─ LLM 调用 (app/llm/provider.py) ──────────────────────────────────────────────┐  │
 │  │  LLMProvider.create_invoker("normal"|"fast", streaming=...)                   │  │
 │  │    └─ ChatOpenAI(base_url=dashscope compatible-mode, stream_usage=True,       │  │
 │  │                  callbacks=[LLMUsageCallbackHandler])                         │  │
 │  │  LLMInvoker.astream_with_tools(messages, tools) ────HTTPS──► DashScope        │  │
 │  │       └─ chunk.tool_call_chunks 增量拼装 → tool_calls → 执行工具               │  │
 │  └───────────────────────────────────────────────────────────────────────────────┘  │
 │                                                                                     │
 │  ┌─ LLM 用量回调 (app/llm/callbacks.py) ─────────────────────────────────────────┐  │
 │  │  on_llm_start → 记时间 ; on_llm_end → 提取 usage → run_coroutine_threadsafe   │  │
 │  │    └─ 后台线程事件循环 ──► UsageRepository.create_log() ──► llm_usage_logs     │  │
 │  └───────────────────────────────────────────────────────────────────────────────┘  │
 └═════════════════════════════════════════════════════════════════════════════════════┘
                                       │
                    SSE 事件逐条回流到 AgentChatWindow.vue 渲染
```

### 1.5 逐跳数据说明（每一跳传什么、什么格式）

| 跳 | 方向 | 数据格式 | 说明 |
|---|---|---|---|
| 1 | 输入框 → composable | `string` | 用户输入的原始文本 |
| 2 | composable → `api/agent.ts` | `POST`，JSON body `{ content: string, session_id?: string }` | `session_id` 为空时后端自动建会话 |
| 3 | 前端 → Vite → FastAPI | HTTP + `Authorization: Bearer <JWT>` | Vite 只做代理，不改写内容 |
| 4 | 路由 → 认证依赖 | `HTTPAuthorizationCredentials` | `HTTPBearer` 自动从 header 提取 |
| 5 | 路由 → Service | `AsyncSession` + 全局单例 | 每请求一个 DB session（`get_db` 生成器） |
| 6 | Service → IntentDetector | `str` → `Intent` dataclass | Redis key `intent:{md5(query)}`，value 是 JSON 字符串 |
| 7 | Service → QueryRewriter | `(query, intent_type)` → `str` | 失败/不满足条件时原样返回 |
| 8 | Service → Registry | `intent_type: str` → `BaseAgent` | 查 `_intent_routing` 字典 |
| 9 | Agent → RAGService | `(query: str, user_id: str)` → `List[Document]` | LangChain `Document`（`page_content` + `metadata`） |
| 10 | RAGService → Milvus | 向量（`text-embedding-v3` 1024 维）+ 标量过滤表达式 | `expr` 支持 `difficulty <= n and category == "x"` |
| 11 | RAGService → PostgreSQL | `select` by `parent_id` in `recipe_documents` | 返回完整父文档（Markdown 正文） |
| 12 | RAGService → Reranker | `(query, List[Document])` → 重排后的 `List[Document]` | HTTP 调 DashScope `gte-rerank` |
| 13 | Agent → LLM | LangChain messages：`[SystemMessage, ...HumanMessage/AIMessage, ToolMessage...]` | tools 以 OpenAI function schema 形式绑定 |
| 14 | LLM → Agent | 流式 `AIMessageChunk`（`content` + `tool_call_chunks`） | 逐 token 增量 |
| 15 | Agent → Service | `dict` 事件：`{type, content|tool_name|arguments|sources}` | 内部事件契约 |
| 16 | Service → 路由 | 同一个 `dict`（经 `asyncio.Queue`） | 哨兵事件 `__agent_result__` / `__agent_error__` 不发给前端 |
| 17 | 路由 → 前端 | SSE 文本帧：`event: <type>\ndata: <json>\n\n` | `ensure_ascii=False`，中文原样输出 |
| 18 | 前端 `api/agent.ts` | `SSEEvent` 对象 | 只解析 `data: ` 行 |
| 19 | composable → UI | `streamingContent += event.content` | 逐字追加，Vue 响应式驱动渲染 |
| 20 | Service → PostgreSQL | `INSERT INTO agent_messages`（`role='assistant'`, `step_number=99`） | 流式结束后才写 |

### 1.6 流式输出是怎么实现的

这个项目里**有两处**关键的「流式桥接」，都用了 `asyncio.Queue` 的生产者-消费者模式——
原因是同一个：**Python 的 `yield` 只能在 `async generator` 里用，而真正干活的函数必须 `return` 结果**，
两者不能并存，于是用队列 + 回调做桥接。

#### 桥接一：Agent 内部 LLM 流式（`app/agent/agent.py`）

```python
thought_queue = asyncio.Queue()

async def on_thought(text: str):
    await thought_queue.put(text)          # 生产者：每收到一个 token

async def run_llm_stream():
    return await self._astream_and_detect_tools(messages, on_thought=on_thought)

llm_task = asyncio.create_task(run_llm_stream())     # 后台跑

while not llm_task.done() or not thought_queue.empty():
    try:
        text = await asyncio.wait_for(thought_queue.get(), timeout=0.05)
        yield {"type": "thought", "content": text}   # 消费者：实时 yield
    except asyncio.TimeoutError:
        continue

tool_calls, full_response, ai_message = await llm_task
```

`timeout=0.05` 是必要的：队列空时不能死等，否则 LLM 还在生成、`llm_task` 尚未完成，
主循环会永久阻塞。退出条件也刻意写成 `not done or not empty`，保证 task 结束后把队列里的残留 token 全部 drain 出来。

#### 桥接二：Agent → SSE 的跨层桥接（`app/agent/service.py`）

```python
event_queue: asyncio.Queue = asyncio.Queue()

async def stream_callback(event_type: str, data: dict):
    await event_queue.put({"type": event_type, **data})   # Agent 侧生产者

async def run_agent():
    try:
        result = await agent.execute(context, stream_callback=stream_callback)
        await event_queue.put({"type": "__agent_result__", "result": result})   # 哨兵
    except Exception as e:
        await event_queue.put({"type": "__agent_error__", "content": str(e)})   # 哨兵

agent_task = asyncio.create_task(run_agent())

while True:
    event = await event_queue.get()
    if event["type"] == "__agent_result__":
        result = event["result"]; break
    elif event["type"] == "__agent_error__":
        yield {"type": "error", ...}; return
    elif event["type"] == "token":
        has_tokens = True; final_answer += event["content"]; yield event
    elif event["type"] == "done":
        final_answer = event.get("content") or final_answer   # 捕获但不透传
    else:
        yield event          # thought / tool_call / observation / sources 直接透传
```

设计要点：

- **`asyncio.Queue` 而非 Redis Pub/Sub**：单进程单事件循环内，队列操作几乎零延迟，
  且不需要额外基础设施。
- **哨兵事件 `__agent_result__` / `__agent_error__`**：用特殊 `type` 把 `AgentResult`
  传回主循环做后处理（存库、补发 sources），避免引入第二条通信通道；因为前缀是 `__`，
  不会被透传给前端。
- **后处理兜底**：不同 Agent 的流式行为不同——`GeneralAgent` 会主动 `stream_callback("sources", ...)`，
  而 `RecipeMasterAgent` 的 sources 只在 `AgentResult` 里。所以主循环结束后会检查
  `if result.sources and not has_sources: yield {...}`，以及
  `if not has_tokens and result.content: yield {"type": "token", ...}`。
- **`done` 事件不直接透传**：Agent 内部的 `done` 只用于捕获最终文本，
  真正的 `done` 由 `AgentService` 在**存库之后**发出（携带 `content`）。

> 历史背景：代码注释（`app/agent/service.py` 第 202-237 行）说明，旧实现用 `collect_events`
> 把所有事件收进 list，等 `agent.execute()` 完全结束才一次性 yield，导致 SSE 格式正确但
> 「瞬间刷出全部内容」，用户仍要等 5-15 秒。当前版本已改为上述并发模型。

### 1.7 上下文压缩在链路中的位置

`app/context/manager.py` 的 `ContextManager` 实现的是「滑动窗口 + 摘要压缩」混合策略：

- `estimate_tokens()`：按 `CHARS_PER_TOKEN = 2` 估算（对中文偏保守，更早触发压缩）；
- `build_context(messages, system_prompt, max_tokens=8192, force_compress=False)`：
  计算 `system + history + current` 的 token 数，
  当 `total > max_tokens * reserve_ratio(0.6)` 或 `turns >= trigger_turns(10)` 时触发压缩；
- 压缩时保留最近 `recent_keep_turns(5)` 轮原文，更早的消息交给
  `app/context/compress.py` 的 `Compressor.compress()`，用 **fast LLM**（`temperature=0.3, max_tokens=500`）
  生成 300 字以内摘要；失败则降级为截断；
- `format_for_llm(context)` 把摘要拼进 system prompt（`[对话历史摘要]` 段），再追加最近消息原文。

**在链路中的理论位置**：应在 `AgentService.stream_agent_chat()` 第 5 步「构建 `AgentContext`」处，
用 `context_manager.build_context(...)` 替换直接截取的 `get_last_n_messages(session_id, 20)`。

**当前实际状态（务必注意）**：`ContextManager` 在 `app/context/manager.py` 末尾以全局单例
`context_manager = ContextManager()` 导出，但 **`ConversationService` 和 `AgentService` 都没有导入它**。
`AgentService` 里 `AgentContext(compressed_summary=None)` 是硬编码的 `None`，
`GeneralAgent._build_messages()` 虽然支持 `compressed_summary`，但永远不会收到非空值。
数据库中 `conversations.compressed_count` / `compressed_summary` 两列也已定义但无写入代码。
**结论：上下文压缩目前是一个「已建成但未接线的组件」，长对话实际靠 `get_last_n_messages(20)` 截断。**

### 1.8 另一条链路：RAG 对话（`/api/v1/conversations/{id}/chat`）

`frontend/src/router/index.ts` 的 `/chat` 与 `/chat/:id` 走这条链路，对应用户侧「标准对话」模式。
它比 Agent 链路简单，**没有工具调用，只有单次 RAG**：

`app/conversation/service.py` → `ConversationService.stream_chat()`：

1. 保存用户消息到 `messages` 表；
2. `get_history_as_dicts(conv_id)` 加载历史；
3. 意图识别（`IntentDetector`，**注意：此处构造时传的是 `redis_client=None`**，所以没有意图缓存）；
4. 查询改写（`QueryRewriter`）；
5. `yield {"type": "thinking", ...}` → `rag_service.retrieve(query=search_query, user_id=...)`；
6. `yield {"type": "sources", ...}`（`_build_sources()` 提取 `dish_name` / `category` / `source` / `relevance_score`）；
7. `_build_llm_messages()` 拼装：
   `[SystemMessage(SYSTEM_PROMPT), ...历史(history[:-1] 避免重复), HumanMessage(检索资料), HumanMessage(当前问题)]`；
8. 在 `with llm_context(module_name="conversation", user_id=..., conversation_id=...)` 上下文里
   `invoker.astream(messages)`，逐 token `yield {"type": "token", ...}`；
9. 保存回答，`yield {"type": "done", "message_id": ...}`。

SSE 事件类型对比：

| 事件 | `/conversations/{id}/chat` | `/agent/chat` |
|---|---|---|
| `session` | ✗ | ✓（首个事件，告知 `session_id`） |
| `thinking` | ✓ | ✓（作为 `thought` 的一种） |
| `intent` | ✗ | ✓ |
| `thought` | ✗ | ✓（ReAct 思考流） |
| `tool_call` / `observation` | ✗ | ✓ |
| `sources` | ✓ | ✓ |
| `token` | ✓ | ✓ |
| `done` | ✓（带 `message_id`） | ✓（带 `content`） |
| `error` | ✓ | ✓ |

### 1.9 组件落地状态核对（不要被注释误导）

阅读本项目时最容易踩的坑：**代码写得完整、注释写得很详尽，但并不等于它被接进了主链路**。
以下三处是实际核对（`Grep` 全仓）后的结论：

| 组件 | 实现位置 | 是否已接入请求链路 | 证据 |
|---|---|---|---|
| 限流 `RateLimiter` | `app/security/middleware/rate_limiter.py` | **否** | `app/main.py` 只在 `lifespan` 里调 `init_rate_limiter(redis_client)` 创建单例；`app.add_middleware()` 只注册了 `CORSMiddleware`，没有限流中间件 |
| Prompt 防护 `PromptGuard` | `app/security/middleware/prompt_guard.py` | **否** | 同上，只有模块级单例 `prompt_guard = PromptGuard()` |
| 统一调度 `guard_manager` | `app/security/guard.py` | **否** | 全仓搜索 `guard_manager.check_request` 只有定义处（`app/security/guard.py:52`）与 `app/security/__init__.py` 的导出，**没有任何 router 调用** |
| 上下文压缩 `ContextManager` | `app/context/manager.py` | **否** | 全仓无 `from app.context import ...` 的实际调用；`AgentService` 硬编码 `compressed_summary=None` |
| Reranker | `app/rag/rerankers/dashscope_reranker.py` | **是** | `init_agent_module()` 创建后调 `rag_service.set_reranker(reranker)`，`RAGService.retrieve()` 第 194 行使用（代码注释明确写了「之前是死代码，P1 修复」） |
| 意图缓存 | `app/conversation/intent.py` | **Agent 链路是，RAG 对话链路否** | `get_agent_service()` 传入 `_redis_client`；`ConversationService.__init__` 传 `redis_client=None` |
| LLM 用量统计 | `app/llm/callbacks.py` | **是** | `LLMProvider.create_llm()` 里 `callbacks=get_usage_callbacks()`，所有 LLM 调用自动带上 |

也就是说：**限流与 Prompt 防护目前是「配置齐备、代码齐备、但需要你手动接线」的半成品状态**。
`config.yml` 的 `security.rate_limit` 段（Agent chat 10 次/分钟、登录 5 次/分钟等）目前不会生效。

---

## 第二部分 代码架构（以 Java MVC / Spring Boot 为参照）

### 2.1 分层结构总览

```
app/
├── main.py                      # 启动类（≈ @SpringBootApplication + main()）
├── config/                      # 配置（≈ application.yml + @ConfigurationProperties）
│   ├── config_loader.py         #   读 config.yml + .env，解析 ${ENV}
│   └── config.py                #   类型化 settings 单例
├── auth/                        # 认证模块
│   ├── router.py                #   Controller
│   ├── service.py               #   Service
│   ├── dependencies.py          #   ≈ Spring Security Filter / @PreAuthorize
│   ├── security.py              #   JWT + bcrypt 工具
│   └── schemas.py               #   DTO (Pydantic)
├── conversation/                # RAG 对话模块
│   ├── router.py  service.py  schemas.py
│   ├── intent.py                #   意图识别
│   ├── query_rewriter.py        #   查询改写
│   └── prompts.py               #   Prompt 模板
├── agent/                       # Agent 模块
│   ├── router.py  service.py  schemas.py  models.py  session_repo.py
│   ├── agent.py                 #   ReActAgent（核心循环）
│   ├── agents/base.py           #   BaseAgent 抽象基类 + AgentContext/AgentResult
│   ├── agents/default.py        #   GeneralAgent
│   ├── subagents/builtin/       #   recipe_master.py / diet_planner.py
│   ├── registry/hub.py          #   AgentRegistry（注册表 + 路由）
│   ├── tools/                   #   @tool 函数集（16 个）
│   └── llm_stats_router.py      #   LLM 用量查询接口
├── diet/                        # 饮食管理模块
│   ├── router.py  service.py  repository.py  models.py  schemas.py
├── rag/                         # RAG 基础设施
│   ├── service.py               #   RAGService（门面 / Facade）
│   ├── pipeline/                #   retrieval.py（混合检索）、document_processor.py（Small-to-Large）
│   ├── cache/cache_manager.py   #   双层缓存
│   ├── embeddings/              #   get_embedding_model()
│   ├── vector_stores/           #   get_vector_store()
│   └── rerankers/               #   base.py + dashscope_reranker.py
├── llm/                         # LLM 接入
│   ├── provider.py              #   LLMProvider / LLMInvoker
│   ├── callbacks.py             #   用量追踪回调
│   └── context.py               #   llm_context() 请求级上下文
├── database/                    # 数据访问层
│   ├── session.py               #   engine + AsyncSessionLocal + Base
│   ├── models.py                #   ORM 实体
│   ├── conversation_repository.py
│   └── usage_repository.py
├── context/                     # 上下文压缩（未接线）
├── security/                    # 限流 / Prompt 防护（未接线）
├── ingestion/                   # 数据摄取管道
└── init.py
```

### 2.2 Java ↔ Python 对照表

| Java / Spring Boot | 本项目 | 具体文件 / 写法 |
|---|---|---|
| View（JSP / Thymeleaf / 独立 SPA） | Vue 3 SPA | `frontend/src/pages/*.vue`、`components/**/*.vue`，通过 REST + SSE 交互 |
| Controller（`@RestController`） | FastAPI `APIRouter` | `app/agent/router.py`、`app/conversation/router.py`、`app/diet/router.py`、`app/auth/router.py`、`app/agent/llm_stats_router.py` |
| `@GetMapping("/x")` | `@router.get("/x")` | 同名装饰器，`@router.post` / `@router.patch` / `@router.delete` |
| `@RequestMapping("/agent")` | `APIRouter(prefix="/agent")` | `app/agent/router.py:36` |
| `@RequestParam` / `@PathVariable` / `@RequestBody` | 函数参数 + Pydantic 模型 | `conv_id: str`（Path）、`week_start: date`（Query）、`body: AddMealRequest`（Body） |
| Service（`@Service`） | `app/*/service.py` 中的类 | `AgentService`、`ConversationService`、`DietService`、`AuthService`；**注意不是 Spring Bean，是每请求 `new` 出来的** |
| Repository / DAO（`@Repository`、JPA Repository、MyBatis Mapper） | `app/database/*_repository.py`、`app/agent/session_repo.py`、`app/diet/repository.py` | 手写 SQLAlchemy 2.0 风格 `select()` 语句；`app/agent/session_repo.py:108` 的注释直接写着「相当于 java 中的 dao 层」 |
| Entity（JPA `@Entity`） | SQLAlchemy `DeclarativeBase` 模型 | `app/database/models.py`（`User` / `Conversation` / `Message` / `LlmUsageLog` / `RecipeDocument`）、`app/agent/models.py`（`AgentSession` / `AgentMessage`）、`app/diet/models.py` |
| DTO + Jackson 序列化 | Pydantic `BaseModel` | `app/conversation/schemas.py`、`app/agent/schemas.py`、`app/diet/schemas.py`；用 `response_model=ConversationResponse` 声明出参 |
| Bean Validation（`@NotNull` / `@Size`） | Pydantic `Field(...)` 约束 | `content: str = Field(..., min_length=1, max_length=5000)`（`app/conversation/schemas.py`）；`limit: int = Query(50, ge=1, le=500)`（`app/agent/llm_stats_router.py`） |
| `@Entity` ↔ DTO 的手工转换 / MapStruct | `_orm_to_dict(obj)` | `app/conversation/service.py:28` 与 `app/agent/service.py:37`，逐列 `getattr`，目的是「切断 session 依赖」 |
| DI 容器（Spring IoC / `@Autowired`） | FastAPI `Depends()` + 模块级全局变量 + `lifespan` 手工装配 | `Depends(get_db)` / `Depends(get_current_user)` / `Depends(get_agent_service)`；全局单例在 `main.py` 创建后由 `init_*_module(...)` 注入 |
| `@Component` / `@Bean` 单例 | 模块级变量 + `init_xxx()` 函数 | `app/security/middleware/rate_limiter.py` 的 `rate_limiter: Optional[RateLimiter] = None` + `init_rate_limiter()` |
| AOP / `HandlerInterceptor` / `Filter` | FastAPI `middleware`（`app.add_middleware`） | `app/main.py:90` 的 `CORSMiddleware`；`GuardManager` 是自实现的「拦截器链」，但**未挂载**（见 1.9） |
| `@ControllerAdvice` + `@ExceptionHandler` | `HTTPException` + 默认异常处理 | 各 router 里 `raise HTTPException(status_code=404, detail="对话不存在")`；未自定义全局 handler |
| `application.yml` / `application.properties` | `config.yml` | 段结构与 Spring 类似：`llm` / `embedding` / `vector_store` / `retrieval` / `cache` / `database` / `security` / `agent` / `rag` / `diet` |
| profile + `${ENV_VAR}` 占位符 | `.env` + `${DATABASE_PASSWORD}` | `app/config/config_loader.py` 的 `_resolve_env_vars()` 递归替换 `${...}`；密钥全部走 `.env` |
| `@ConfigurationProperties` | 手写的配置类 | `app/config/config.py`：`LLMConfig` / `PostgresConfig` / `RateLimitConfig` 等，逐段 `data.get(...)` |
| `pom.xml` / `build.gradle` | `requirements.txt` | 注意：**没有版本范围/传递依赖解析**，`fastapi==0.115.0` 这类精确 pin |
| Maven 坐标 `groupId:artifactId:version` | pip 包名 + 版本 | `langchain-openai==1.1.0` 等 |
| 启动类 `main()` + `@SpringBootApplication` | `app/main.py` + `FastAPI(lifespan=lifespan)` | `uvicorn app.main:app --reload --port 8000` |
| `ApplicationRunner` / `@PostConstruct` | `lifespan` 启动钩子（`@asynccontextmanager`） | `app/main.py:40`，`init_db()` → `init_rate_limiter()` → `rag_service.initialize()` → `init_conversation_module()` → `init_agent_module()` → `init_diet_module()` |
| `@PreDestroy` / `DisposableBean` | `lifespan` 的 `yield` 之后 | `await close_db()` |
| `@Async` / `CompletableFuture` | `asyncio.create_task()` | `app/agent/service.py:285` 的 `agent_task = asyncio.create_task(run_agent())` |
| `@Scheduled` | 无（项目里没有定时任务） | — |
| 线程池 / `ExecutorService` | `asyncio.to_thread()` | 把同步的 LangChain 调用丢到线程池，见 `app/rag/pipeline/retrieval.py:132` 与 `app/rag/cache/cache_manager.py:101` |
| JUnit + `@Test` | `unittest` | `tests/test_diet_logic.py`、`tests/test_meal_plan_tools.py` |
| Lombok（`@Data` / `@Builder`） | `@dataclass` | `Intent`、`IntentFilters`（`app/conversation/intent.py`）、`AgentContext` / `AgentResult` / `Source`（`app/agent/agents/base.py`） |
| `interface` + 多实现 | `abc.ABC` + `@abstractmethod` / `typing.Protocol` | `app/agent/agents/base.py:67` 的 `BaseAgent(ABC)`；`app/rag/rerankers/base.py` 的 `BaseReranker` |

### 2.3 Python 项目里与 Java 不同的关键约定

Java 开发者最容易困惑的十件事，逐条对应到本项目的真实代码：

#### (1) 没有编译期接口约束，抽象靠「约定 + 运行时」

Java 里 `interface` 由编译器强制；Python 的 `abc.ABC` + `@abstractmethod` **只在实例化时**才报错，
而 `typing.Protocol` 完全是静态检查器（mypy/pyright）的事，运行时毫无约束。

本项目的做法是**用 ABC 保持可读性**：

```python
# app/agent/agents/base.py
class BaseAgent(ABC):
    name: str = ""
    intent_match: List[str] = []

    @abstractmethod
    async def execute(self, context: AgentContext, stream_callback: Optional[Callable] = None) -> AgentResult: ...
```

`RecipeMasterAgent` / `GeneralAgent` / `DietPlannerAgent` 都继承它。新增 Agent 只需实现
`execute()` + 声明 `intent_match`，然后 `registry.register(instance)` 即可——这就是注册表模式
（`app/agent/registry/hub.py`）替代硬编码 `if/else` 的用意。

#### (2) `__init__.py` 充当 package 声明

Java 用 `package com.foo.bar;` 声明包。Python 里**目录名本身就是包名**，
`__init__.py` 的作用是「标记这是个包」+「做包的统一导出」，相当于 Spring 里的 `@Configuration` 再导出一批 Bean：

```python
# app/agent/__init__.py —— 整个文件只有两行
from app.agent.router import router as agent_router, init_agent_module
__all__ = ["agent_router", "init_agent_module"]
```

于是 `main.py` 里可以直接 `from app.agent import agent_router`，而不必写全路径。
`from app.config import settings` 这个到处出现的导入，靠的也是 `app/config/__init__.py`
把 `config.py` 里的 `settings` 单例再导出一次。这就是**替换 Java `import com.foo.Bar;` 的惯用法**。

#### (3) 模块即单例（Module-as-Singleton）

Java 的单例要自己实现（饿汉/静态内部类/枚举）或交给 Spring 容器。Python **不需要**：
模块在 `sys.modules` 中只会被导入执行一次，因此模块级变量天然是单例。

```python
# app/config/config.py 最后一行
settings = Settings()          # 全应用共享同一个配置对象

# app/context/manager.py 最后一行
context_manager = ContextManager()

# app/security/middleware/prompt_guard.py 最后一行
prompt_guard = PromptGuard()

# app/agent/registry/hub.py
_global_registry: Optional[AgentRegistry] = None
def get_agent_registry() -> AgentRegistry:   # 懒汉式单例
    global _global_registry
    if _global_registry is None:
        _global_registry = AgentRegistry()
    return _global_registry
```

对应到 Java：**「模块级单例」≈ 直接用静态字段持有 Bean，绕开了 IoC 容器。**

#### (4) 鸭子类型 / Protocol：不关心你是谁，只关心你会什么

Java 的泛型与接口是标称类型系统（nominal typing），`A` 必须显式 `implements B`。
Python 是结构类型（structural typing）：只要对象有 `.name` 和 `.execute()`，它就能当 Agent 用。

本项目的体现：`app/agent/tools/__init__.py` 的 `get_all_tools()` 返回一个混合列表——
里面有 `@tool` 装饰的 async 函数、同步函数、LangChain `BaseTool` 对象。
而 `ReActAgent.run()` 调用它们的写法是**运行时探测**：

```python
if hasattr(tool_func, "ainvoke"):
    observation = await tool_func.ainvoke(tool_args)      # LangChain @tool
elif hasattr(tool_func, "__call__"):
    result = tool_func(**tool_args)
    if hasattr(result, "__await__"):                       # 是不是协程？
        observation = await result
    else:
        observation = result
```

Java 里这段会被写成接口 + 多个实现类；Python 里就是 `hasattr` 一把梭。

#### (5) 装饰器 ≈ 注解，但**运行时可调用**（这是最大区别）

这是 Java 开发者最容易误解的地方：

| | Java 注解 | Python 装饰器 |
|---|---|---|
| 是什么 | 元数据（metadata） | **一个接收函数、返回新函数的可调用对象** |
| 谁处理 | 反射 + 容器（Spring） | 装饰时直接执行，或在包装函数里运行时执行 |
| 生效时机 | 由框架扫描发现 | 定义时立即生效（`@decorator` 等价于 `f = decorator(f)`） |

本项目三种典型用法：

```python
# ① 路由注册：FastAPI 在导入时收集路由表（真正"扫描"的是导入语句本身）
@router.post("/chat")
async def agent_chat(...): ...

# ② 工具声明：LangChain 用 @tool 把函数包成带 name/description/args_schema 的 BaseTool
@tool
async def search_recipes(...) -> str: ...

# ③ 上下文管理：@asynccontextmanager 把 async generator 变成上下文管理器
@asynccontextmanager
async def lifespan(app: FastAPI): ...
```

`@tool` 后函数**不再是普通函数**，而是 `StructuredTool` 对象——所以
`ReActAgent.__init__` 才能做 `self._tool_map = {tool.name: tool for tool in self.tools}` 和
`tool.args_schema.model_fields`（这些属性是装饰器附加的）。

#### (6) `async def` / `await` ≈ 异步非阻塞（对比 `CompletableFuture` / Reactor）

| Java | Python | 本项目 |
|---|---|---|
| `CompletableFuture<T>` | `Coroutine`（`async def` 的返回值） | `await rag_service.retrieve(...)` |
| `future.thenApply(...)` | `await`（挂起点） | `async for chunk in invoker.astream(...)` |
| `Flux<T>` / `Mono<T>` 流 | `AsyncIterator` / `AsyncGenerator` | `AsyncIterator[Dict[str, Any]]` 作为 SSE 事件流 |
| `Executors.newFixedThreadPool` | `asyncio.to_thread()` | 包住同步的 LangChain Milvus 调用 |
| `@Async` 返回 `Future` | `asyncio.create_task()` | `agent_task = asyncio.create_task(run_agent())` |
| `CountDownLatch` / `BlockingQueue` | `asyncio.Queue` | `event_queue` 生产者-消费者（见 1.6） |

**核心心智转换**：Java 的异步是「线程 + 回调/Future 组装」；Python 的 `asyncio` 是
**单线程事件循环 + 显式 `await` 挂起点**。所有 I/O 都必须是异步的才会并发——
这就是为什么 `app/database/session.py` 用 `create_async_engine` + `AsyncSession`，
而不是同步的 SQLAlchemy Session。

> 顺带解释一个「坑」（README 与 `config.yml` 都提到）：**Windows 上 `uvicorn` 必须带 `--reload`**。
> 原因是 `psycopg` 的异步模式不能在默认的 `ProactorEventLoop` 上跑。
> 只有当 `use_subprocess=True`（即开启 `--reload`）时，uvicorn 才会切到 `WindowsSelectorEventLoopPolicy`。
> 代码里 `app/main.py:7-8` 也补了一道保险：
> `if sys.platform == "win32": asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())`。

#### (7) 为什么用 Pydantic 做运行时校验（对比 Java 编译期类型 + Bean Validation）

Java 的类型错误在**编译期**就被 javac 拦下；`@Valid` + Hibernate Validator 在**运行时**再做语义校验。
Python 没有编译期检查，所以**两件事都压在运行时**：类型提示（type hints）只是给 IDE/mypy 看的，
真正保证输入合法的是 Pydantic：

```python
# app/conversation/schemas.py
class ChatRequest(BaseModel):
    content: str = Field(..., min_length=1, max_length=5000)
```

FastAPI 在请求进入函数体**之前**就完成解析 + 校验，不合法直接返回 422，
并把校验错误结构化输出。等价于 Java 的「Jackson 反序列化 + `@Valid` + `MethodArgumentNotValidException`」，
但在 Python 里是同一套机制、一个类搞定。

出参方向也一样：`response_model=ConversationResponse` 让 FastAPI 自动做
序列化 + 字段裁剪（比如密码哈希永远不会泄漏）。这是 Pydantic 的第二个作用。

#### (8) 「手工版 IoC」——本项目的 DI 到底怎么做的

这是本项目与 Spring 差异最大、也最值得单独说的地方。Spring 里你写 `@Service`，
容器负责「创建 + 装配 + 生命周期」；本项目是**三个层次的手工装配**：

```
① 全局单例（在 import 时创建，或由 lifespan 创建）
   main.py:  llm_provider = LLMProvider()
             rag_service  = RAGService(redis_client)

② 注入函数（模块级 global，把①塞进各模块的私有变量）
   init_conversation_module(llm_provider, rag_service)
   init_agent_module(llm_provider, rag_service, redis_client)
   init_diet_module(llm_provider)

③ 请求级依赖（FastAPI Depends 负责"每次请求 new 一个"）
   def get_agent_service(db = Depends(get_db)) -> AgentService:
       return AgentService(db, _llm_provider, _rag_service, _redis_client)
```

为什么不用真正的 DI 框架？看代码注释能读出三条理由：

- **短生命周期对象不适合做全局单例**：`AgentService` / `ConversationService` / `DietService`
  都持有 `AsyncSession`（一个请求一个事务），必须每请求新建，不能像 Spring 那样默认 singleton。
  **「Service 是 prototype scope」是本项目与 Spring 最直观的差异。**
- **长生命周期对象恰恰应该只建一次**：`LLMProvider`（无状态）、`RAGService`（持有 Milvus 连接与
  Embedding 模型，加载一次约 100MB）、`AgentRegistry`（注册表）。
- **装配顺序有依赖**：`RAGService.initialize()` 必须先连上 Milvus，之后
  `init_agent_module()` 才能把 Reranker 注入进去（`rag_service.set_reranker(reranker)`）。
  用 `lifespan` 显式排好顺序，比让容器去猜依赖图更可控——这也是注释里
  「为什么启动时创建而不是每次请求创建？Agent 实例是无状态的，启动时创建一次即可复用」的由来。

代价也很明显：**没有 Spring 的自动扫描，新增模块必须记得手工在 `lifespan` 里加一行注入**，
否则就会像 1.9 节那些组件一样「代码在但没接线」。

#### (9) 类型注解的三种「语气」

读 Python 代码时要分清类型提示是「承诺」还是「建议」：

```python
_llm_provider: LLMProvider = None          # ← 声明是 LLMProvider，实际赋 None（不检查！）
self.retrieval: Optional[RetrievalOptimizationModule] = None   # ← 诚实写法
def get_usage_callbacks() -> List[BaseCallbackHandler]: ...    # ← 真实返回值
async def _get_llm(self):                  # ← 干脆不写返回类型
```

`app/conversation/router.py:38` 的 `_llm_provider: LLMProvider = None` 就是「名义上非空、实际先给 None
等 `init_*` 注入」的典型。Java 里这相当于 `@Autowired private Foo foo;`（由容器保证注入完成），
但 Python 没有任何东西保证——**这就是手工 DI 的实际风险点**。

### 2.4 代码走查：用户问一句菜谱，代码是怎么走的

以用户输入「番茄炒蛋怎么做」、走 Agent 链路为例，顺着文件与函数读：

| # | 文件 | 函数 / 类 | 做什么 |
|---|---|---|---|
| 1 | `frontend/src/components/chat/ChatInput.vue` | `@send` emit | 用户回车，向上 emit `send(content)` |
| 2 | `frontend/src/pages/ChatPage.vue` | `handleSend(content)` | 判断 `isAgentMode`，调 `sendAgentMessage` |
| 3 | `frontend/src/composables/useAgent.ts` | `sendMessage(content)` | push 用户消息、开 `AbortController`、进入 `for await` |
| 4 | `frontend/src/api/agent.ts` | `streamAgentChat(sessionId, message, signal)` | `fetch POST /api/v1/agent/chat`，手写 SSE 解析器 |
| 5 | `frontend/vite.config.ts` | `server.proxy` | 把 `/api` 转发到 `http://localhost:8000` |
| 6 | `app/main.py` | `app.include_router(agent_router, prefix="/api/v1")` | 路由挂载 |
| 7 | `app/agent/router.py` | `agent_chat(body, current_user, service)` | 校验/创建会话，返回 `StreamingResponse(event_stream())` |
| 8 | `app/auth/dependencies.py` | `get_current_user()` → `decode_access_token()` | JWT 解出 `user_id`，查 `users` 表返回 `User` |
| 9 | `app/agent/router.py` | `get_agent_service(db)` | 组装 `AgentService(db, _llm_provider, _rag_service, _redis_client)` |
| 10 | `app/agent/service.py` | `AgentService.__init__()` | `inject_rag_service(rag_service)`；建 `IntentDetector`（带 Redis）与 `QueryRewriter` |
| 11 | `app/agent/service.py` | `stream_agent_chat()` Step 1 | `repo.get_last_n_messages(session_id, 20)`（**先于**保存当前消息） |
| 12 | `app/agent/session_repo.py` | `get_last_n_messages()` | `select(AgentMessage)...order_by(created_at.desc()).limit(n)` 后 `reversed` |
| 13 | `app/agent/service.py` | `repo.add_message(role="user", step_number=0)` | 写入 PostgreSQL `agent_messages` |
| 14 | `app/conversation/intent.py` | `IntentDetector.detect(query)` | 查 Redis `intent:{md5}` → miss → `_call_llm()` → `_parse_result()` |
| 15 | `app/llm/provider.py` | `LLMProvider.create_invoker("fast", streaming=False)` | 建 `ChatOpenAI(model=qwen-plus, callbacks=[usage_handler])` |
| 16 | `app/conversation/prompts.py` | `INTENT_CLASSIFICATION_PROMPT` | 意图分类的 System Prompt |
| 17 | `app/conversation/query_rewriter.py` | `QueryRewriter.rewrite(query, intent_type)` | 意图是 `recipe_search` → 调 fast LLM 改写 → 新查询 |
| 18 | `app/agent/registry/hub.py` | `get_agent_registry().match("recipe_search")` | 查表 → 返回 `RecipeMasterAgent` 实例 |
| 19 | `app/agent/subagents/builtin/recipe_master.py` | `RecipeMasterAgent.execute(context, stream_callback)` | 取 `context.rewritten_query`；把历史转成 LangChain 消息 |
| 20 | `app/agent/subagents/builtin/recipe_master.py` | `ReActAgent(llm_invoker, tools, max_iterations=8, system_prompt=RECIPE_MASTER_PROMPT)` | 创建带专用 Prompt 的 ReAct Agent |
| 21 | `app/agent/tools/__init__.py` | `get_all_tools()` | 返回 16 个 `@tool` 对象 |
| 22 | `app/agent/agent.py` | `ReActAgent.run(user_message, user_id, context_messages)` | 进入 ReAct 循环 |
| 23 | `app/agent/agent.py` | `_astream_and_detect_tools(messages, on_thought)` | 调 `llm_invoker.astream_with_tools(...)`，累积 `tool_call_chunks` |
| 24 | `app/llm/provider.py` | `LLMInvoker.astream_with_tools()` | `bind(model=..., tools=[...])` 后 `astream` |
| 25 | `app/agent/agent.py` | `_extract_tool_calls` / 组装 | 解析出 `{"name": "search_recipes", "args": {...}}` |
| 26 | `app/agent/tools/recipe_tools.py` | `search_recipes(...)`（`@tool`） | 内部调注入的 `rag_service.retrieve()` |
| 27 | `app/rag/service.py` | `RAGService.retrieve(query, user_id)` | 见下 28-33 |
| 28 | `app/rag/cache/cache_manager.py` | `CacheManager.get(query, user_id)` | L1 Redis → L2 Milvus（0.92 阈值） |
| 29 | `app/rag/pipeline/retrieval.py` | `intelligent_ranker_selection(query)` | 含「怎么做」→ `("weighted", [0.4, 0.6])` |
| 30 | `app/rag/pipeline/retrieval.py` | `hybrid_search(query, top_k, ...)` | `asyncio.to_thread` 调 Milvus `similarity_search_with_score` |
| 31 | `app/rag/embeddings/embedding_factory.py` | `get_embedding_model("text-embedding-v3")` | 查询向量化 |
| 32 | `app/rag/pipeline/document_processor.py` | `post_process_retrieval(chunks)` | 按 `parent_id` 回表 `recipe_documents`，去重排序 |
| 33 | `app/rag/rerankers/dashscope_reranker.py` | `DashScopeReranker.rerank(query, documents, top_n)` | HTTP 调 `gte-rerank` 精排，失败降级 |
| 34 | `app/agent/agent.py` | `ToolMessage(content=str(observation), tool_call_id=...)` | 把工具结果作为 Observation 追加进 `messages`，进入下一轮 |
| 35 | `app/agent/agent.py` | 无 `tool_calls` 时 `yield {"type": "done", "content": full_response}` | ReAct 循环结束 |
| 36 | `app/llm/callbacks.py` | `LLMUsageCallbackHandler.on_llm_end()` | 提取 `usage_metadata`，`run_coroutine_threadsafe` 写入 `llm_usage_logs` |
| 37 | `app/agent/service.py` | `stream_callback()` → `event_queue.put()` | 每个事件推入队列 |
| 38 | `app/agent/service.py` | `while True: event = await event_queue.get()` | 主循环消费，`yield` 给路由 |
| 39 | `app/agent/router.py` | `event_stream()` | `yield f"event: {t}\ndata: {json.dumps(...)}\n\n"` |
| 40 | `frontend/src/api/agent.ts` | `yield JSON.parse(data) as SSEEvent` | 解析回对象 |
| 41 | `frontend/src/composables/useAgent.ts` | `switch (event.type)` | `token` 累加到 `streamingContent`；`done` 时把整条消息 + `thoughts` 归档 |
| 42 | `frontend/src/components/agent/AgentChatWindow.vue` | 渲染 | `marked` 渲染 Markdown，`AgentThinkingBlock.vue` 展示思考/工具调用步骤 |
| 43 | `app/agent/service.py` | Step 8 | `repo.add_message(role="assistant", content=final_answer, step_number=99)` + `update_session_title()` |

### 2.5 后端分层依赖关系图

依赖方向严格**单向向下**，不存在下层反向 import 上层的情况
（唯一的例外通过「依赖注入函数」打破，见图中虚线）。

```
                  ┌──────────────────────────────────────────┐
                  │   frontend/ (Vue 3 SPA)                  │
                  └────────────────────┬─────────────────────┘
                                       │ HTTP / SSE
                                       ▼
 ╔═════════════════════════════════════════════════════════════════════════════╗
 ║ ① 表现层 / Controller                                                        ║
 ║   app/main.py (FastAPI + lifespan + CORSMiddleware + include_router)         ║
 ║   app/auth/router.py        app/conversation/router.py                       ║
 ║   app/agent/router.py       app/diet/router.py    app/agent/llm_stats_router.py
 ╚════════════════════════════════╤════════════════════════════════════════════╝
                                  │ Depends(get_db) / Depends(get_*_service)
                                  │ Depends(get_current_user)  ← app/auth/dependencies.py
                                  ▼
 ╔═════════════════════════════════════════════════════════════════════════════╗
 ║ ② 业务层 / Service                                                           ║
 ║   app/auth/service.py          AuthService                                   ║
 ║   app/conversation/service.py  ConversationService ──► IntentDetector        ║
 ║   app/agent/service.py         AgentService ──────────► QueryRewriter         ║
 ║   app/diet/service.py          DietService                                    ║
 ╚════════════════════════════════╤════════════════════════════════════════════╝
                                  │
        ┌─────────────────────────┼───────────────────────────┬────────────────┐
        ▼                         ▼                           ▼                ▼
 ┌──────────────┐   ┌────────────────────────┐   ┌────────────────┐  ┌──────────────────┐
 │ ③a Agent 层  │   │ ③b RAG 层              │   │ ③c LLM 层      │  │ ③d Repository 层 │
 │              │   │                        │   │                │  │                  │
 │ AgentRegistry│   │ RAGService (门面)       │   │ LLMProvider    │  │ ConversationRepo │
 │  └ agents/   │   │  ├ CacheManager        │   │  └ LLMInvoker  │  │ AgentSessionRepo │
 │    base.py   │   │  ├ RetrievalOptimiz..  │   │     (fast/     │  │ DietRepository   │
 │    default.py│   │  ├ DocumentProcessor   │   │      normal,   │  │ UsageRepository  │
 │    subagents/│   │  ├ get_embedding_model │   │      tool      │  │                  │
 │  └ ReActAgent│   │  ├ get_vector_store    │   │      calling)  │  │ SQLAlchemy       │
 │    (agent.py)│   │  └ DashScopeReranker   │   │ CallbackHandler│  │  select() 语句    │
 │  └ tools/    │   │                        │   │ llm_context()  │  │                  │
 │    (@tool×16)│   │                        │   │                │  │                  │
 └──────┬───────┘   └───────────┬────────────┘   └───────┬────────┘  └────────┬─────────┘
        │                       │                        │                    │
        └───────────────────────┴────────────┬───────────┴────────────────────┘
                                             │
                                             ▼
 ╔═════════════════════════════════════════════════════════════════════════════╗
 ║ ④ 基础设施层                                                                 ║
 ║  app/database/session.py   create_async_engine + AsyncSessionLocal + Base     ║
 ║  app/database/models.py    ORM 实体  │ app/agent/models.py │ app/diet/models.py ║
 ║  app/config/config.py      settings 单例 │ app/llm/context.py contextvars     ║
 ╚═════════════════════════════════════════════════════════════════════════════╝
                                             │
        ┌────────────┬───────────────┬───────┴────────┬───────────────┬──────────┐
        ▼            ▼               ▼                ▼               ▼          ▼
   ┌─────────┐ ┌──────────┐   ┌───────────┐   ┌────────────┐  ┌──────────┐ ┌─────────┐
   │PostgreSQL│ │ Milvus   │   │  Redis    │   │ DashScope  │  │ Tavily / │ │本地文件  │
   │  :5433  │ │  :19530  │   │  :6379    │   │ LLM API    │  │ DuckDuck │ │ ICS/MD/ │
   │         │ │          │   │           │   │ + Rerank   │  │ Go 搜索  │ │ HTML    │
   └─────────┘ └──────────┘   └───────────┘   └────────────┘  └──────────┘ └─────────┘
```

图中**虚线依赖（由 `init_*_module()` 手工注入，绕过了正常的 import 方向）**：

```
main.py ──init_conversation_module(llm, rag)──► conversation/router.py 的模块级 _llm_provider / _rag_service
main.py ──init_agent_module(llm, rag, redis)──► agent/router.py 的模块级全局变量
                                            └──► inject_rag_service() / inject_meal_plan_deps()
                                                 / inject_diet_tools_deps()  ──► tools/ 内部私有变量
main.py ──init_diet_module(llm)───────────────► diet/router.py 的 _llm_provider
main.py ──init_rate_limiter(redis)────────────► security/middleware/rate_limiter.py 的 rate_limiter
```

**为什么需要这层「注入」而不是直接 import？**
因为 `agent/tools/recipe_tools.py` 需要 `RAGService`，而 `RAGService` 需要在启动时 `initialize()`
（连 Milvus、加载模型）。如果工具模块直接 `from app.rag.service import rag_service`，
会形成**循环导入**并造成「导入时就要求外部资源已就绪」。所以采用
「模块级私有变量 + setter/init 函数」的标准做法——功能上等价于 Spring 的
`@Autowired` 字段注入，只是**依赖方向由编译期变成运行期**。

---

## 附录：快速上手与本文的核对方式

**启动命令**（详见 `README.md` 与 `start.bat`）：

```bash
# 基础设施（Docker，来自 D:\agent-cook\CookHero\deployments\docker-compose.yml）
docker compose up -d          # PostgreSQL:5433 / Milvus:19530 / Redis:6379 / MinIO

# 后端（Windows 上 --reload 是必须的，见 2.3(6)）
uvicorn app.main:app --reload --port 8000

# 前端
cd frontend && npm install && npm run dev     # :5173
```

**离线验证**：`tests/test_diet_logic.py`、`tests/test_meal_plan_tools.py` 是 `unittest` 用例，
无需外部服务即可运行。

**本文的核对方式**：所有路径、类名、函数名均通过 `Read`/`Grep` 核对源码得到；
「未接线」结论通过全仓 `Grep` 确认无调用点；`config.yml` 的每一项配置都与
`app/config/config.py` 中的读取代码逐条比对。凡有不确定处（例如 L2 缓存的实际命中率、
生产环境是否启用 `--reload`），本文选择不写而非猜测。
