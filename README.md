<div align="center">

# 🍳 CookAgent

**AI 智能烹饪助手 | RAG + ReAct Agent 驱动的多模态烹饪问答系统**

[![Python](https://img.shields.io/badge/Python-3.11+-blue.svg)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115-009688.svg)](https://fastapi.tiangolo.com/)
[![Vue](https://img.shields.io/badge/Vue-3.x-4FC08D.svg)](https://vuejs.org/)
[![Milvus](https://img.shields.io/badge/Milvus-2.4-00A1EA.svg)](https://milvus.io/)
[![License](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

*"帮我找几个快手的减脂菜" → 智能检索、精准回答、多轮对话*

</div>

---

## 📖 项目简介

CookAgent 是一个基于 **RAG（检索增强生成）** 和 **ReAct Agent** 架构的智能烹饪助手。它从开源菜谱数据集 [HowToCook](https://github.com/Anduin2017/HowToCook) 中摄取数千道菜谱，通过向量检索 + LLM 推理，为用户提供：

- 🔍 **智能菜谱搜索** — 语义理解，不靠关键词匹配
- 👨‍🍳 **烹饪问答** — 步骤指导、技巧解答
- 🥗 **食材安全查询** — 过敏原检测、禁忌提醒
- 📊 **营养估算** — 卡路里与营养成分预估
- 🔄 **食材替换建议** — 缺少某样食材？找替代方案

项目借鉴了 [CookHero](https://github.com/Decade-qiu/CookHero) 的架构设计，在此基础上进行了全面重构与功能扩展。

> **参考项目：** [CookHero](https://github.com/Decade-qiu/CookHero) — 感谢原作者的开源贡献 🙏

---

## 🏗️ 架构概览

```
┌──────────────────────────────────────────────────────────────────┐
│                       Frontend (Vue 3)                            │
│          SSE 流式对话 · 双模式切换 · Agent 思维链可视化            │
└────────────────────────┬─────────────────────────────────────────┘
                         │ HTTP + SSE
┌────────────────────────┴─────────────────────────────────────────┐
│                     Backend (FastAPI)                              │
│                                                                    │
│  ┌──────────┐  ┌──────────────────────┐  ┌────────────────────┐  │
│  │  Auth    │  │  Conversation (RAG)  │  │   Agent Service    │  │
│  │  Service │  │  + IntentDetector    │  │   + IntentDetector │  │
│  │          │  │  + QueryRewriter     │  │   + QueryRewriter  │  │
│  └──────────┘  └──────────┬───────────┘  └──────────┬─────────┘  │
│                           │                         │             │
│              ┌────────────┴──────┐      ┌───────────┴─────────┐  │
│              │   RAG Pipeline    │      │   Agent Registry    │  │
│              │  · Hybrid Search  │      │   · RecipeMaster    │  │
│              │  · Reranker       │      │   · GeneralAgent    │  │
│              │  · Dual Cache     │      │   · DietPlanner (P2)│  │
│              └────────┬──────────┘      └───────────┬─────────┘  │
│                       │                             │             │
│  ┌────────────────────┼─────────────────────────────┼─────────┐  │
│  │       Middleware: Rate Limiter + Prompt Guard              │  │
│  └────────────────────────────────────────────────────────────┘  │
└──────────┬──────────────┬──────────────┬──────────────────────────┘
           │              │              │
    ┌──────┴──────┐  ┌────┴──────┐  ┌───┴───────┐
    │ PostgreSQL  │  │  Milvus   │  │   Redis   │
    │ + pgvector  │  │ (Vector)  │  │  (Cache)  │
    └─────────────┘  └───────────┘  └───────────┘
```

### 核心技术路线

| 层级 | 技术选型 | 说明 |
|------|---------|------|
| **LLM 推理** | Qwen-Plus / Qwen-Max（DashScope） | 两层分级：快速层处理意图检测，标准层生成回答 |
| **Embedding** | DashScope text-embedding-v3 | 1024 维中文语义向量 |
| **向量检索** | Milvus 2.4 混合搜索 | Dense（语义）+ Sparse（BM25 关键词）加权融合 |
| **检索精排** | gte-rerank | P1 新增：对候选结果二次排序，提升 Top5 精度 |
| **检索后处理** | Small-to-Large | 小块精准匹配 → 父文档完整上下文 |
| **双层缓存** | Redis L1（精确匹配）+ Milvus L2（语义去重） | 亚毫秒命中 + 相似查询检测 |
| **Agent 模式** | ReAct 循环 + Agent Registry 路由 | P1 升级：意图识别 → 查询改写 → Agent 选择 → 执行 |
| **意图识别** | Fast LLM 分类 | P1 新增：识别 recipe_search / cooking_help / diet_plan / general_chat |
| **查询改写** | Fast LLM 改写 | P1 新增：口语转检索语，提升 RAG 命中率 |

---

## ✨ 核心特性

### 🔗 RAG 智能检索
- **混合搜索**：Dense 语义向量 + BM25 稀疏向量，智能权重自动适配查询类型
- **Reranker 精排**：gte-rerank 对候选结果二次排序，提升检索精度
- **Small-to-Large**：小块向量匹配精度高，大块上下文完整性好，两全其美
- **双层缓存**：Redis 毫秒级精确命中 + Milvus 语义相似度去重，大幅降低 LLM 调用成本
- **元数据过滤**：支持按类别、难度等条件精准筛选，由意图检测自动生成过滤条件

### 🤖 ReAct Agent
- **6 个内置工具**：菜谱搜索、详情查阅、食材安全、营养估算、食材替换、用户信息
- **Agent Registry 路由**：意图识别 → 自动选择 RecipeMasterAgent / GeneralAgent
- **可视化推理过程**：前端实时展示 Agent 的思考 → 行动 → 观察链路
- **自动注入上下文**：用户 ID、偏好等信息自动注入工具调用，防止幻觉

### 🧠 意图识别与查询改写 (P1)
- **IntentDetector**：Fast LLM 分类用户意图，~200ms 延迟
- **QueryRewriter**：口语转结构化检索语，提升 RAG 检索命中率
- **兜底机制**：LLM 超时自动 fallback 到默认意图，不影响主流程

### 🛡️ 安全防护
- **滑动窗口限流**：按端点独立配置，避免边界突发
- **双重 Prompt Guard**：正则规则（零延迟）+ LLM 语义检测（按需开启）
- **审计日志**：所有安全事件异步记录，可追溯

### 📝 上下文管理
- **滑动窗口 + LLM 摘要压缩**：超长对话自动压缩，兼顾信息完整性与 Token 成本
- **增量压缩**：渐进式合并旧摘要与新消息

### 🔐 用户系统
- JWT 认证 + bcrypt 密码哈希
- 防用户名枚举（统一错误提示）
- 防时序攻击（恒定时间密码比对）

---

## 📁 项目结构

```
CookAgent/
├── app/                                  # 后端应用
│   ├── main.py                           # FastAPI 入口
│   ├── config/                           # 配置管理（YAML + .env）
│   ├── auth/                             # 认证模块（注册/登录/JWT）
│   │
│   ├── agent/                            # ReAct Agent 模块
│   │   ├── agent.py                      # ReActAgent 核心循环
│   │   ├── router.py                     # Agent API 路由 + 初始化
│   │   ├── service.py                    # AgentService（P1 重构）
│   │   ├── session_repo.py               # Agent 会话持久化
│   │   ├── tools/                        # 工具函数集合（6 工具）
│   │   ├── agents/                       # P1 新增：Agent 实例
│   │   │   ├── base.py                   # AgentContext / AgentResult / BaseAgent
│   │   │   └── default.py                # GeneralAgent（兜底）
│   │   ├── subagents/builtin/            # P1 新增：子代理
│   │   │   ├── recipe_master.py          # RecipeMasterAgent（ReAct + 自定义提示词）
│   │   │   └── diet_planner.py           # DietPlannerAgent（P1 骨架 / P2 完整）
│   │   └── registry/                     # P1 新增：Agent 注册中心
│   │       └── hub.py                    # AgentRegistry 路由 + 全局单例
│   │
│   ├── conversation/                     # RAG 对话模块
│   │   ├── router.py                     # 对话 API 路由
│   │   ├── service.py                    # ConversationService（P1 增强）
│   │   ├── intent.py                     # P1 新增：IntentDetector 意图检测
│   │   ├── query_rewriter.py             # P1 新增：QueryRewriter 查询改写
│   │   └── prompts.py                    # P1 新增：意图检测提示词模板
│   │
│   ├── rag/                              # RAG 管线
│   │   ├── embeddings/                   # Embedding 工厂
│   │   ├── vector_stores/                # Milvus 向量存储
│   │   ├── pipeline/                     # 检索 + 文档处理
│   │   │   └── reranker.py               # P1 新增：DashScope Reranker
│   │   └── cache/                        # 双层缓存管理
│   │
│   ├── llm/                              # LLM Provider 层（两层分级）
│   ├── database/                         # 数据库层（SQLAlchemy 异步）
│   ├── security/                         # 安全模块（限流/守卫/审计）
│   ├── context/                          # 上下文管理（滑动窗口/压缩）
│   └── ingestion/                        # 数据摄取管线
│
├── frontend/                             # Vue 3 前端
│   └── src/
│       ├── pages/                        # 页面（登录/注册/对话）
│       ├── components/                   # 组件（侧栏/聊天/Agent 展示）
│       ├── composables/                  # 状态管理（Auth/Conversation/Agent）
│       ├── api/                          # API 客户端 + SSE 流处理
│       └── types/                        # TypeScript 类型定义
│
├── data/howtocook/dishes/                # 菜谱数据（Markdown）
├── docs/                                 # 架构设计文档
│   ├── p1-system-architecture.md         # P1 系统架构文档
│   └── p2-design-plan.md                 # P2 设计方案（含前端配置）
├── scripts/
│   └── ingest_data.py                    # 数据摄取 CLI
├── docker-compose.yml                    # 本地开发环境
├── config.yml                            # 主配置文件（P1/P2 配置项）
├── .env.example                          # 环境变量模板
└── requirements.txt                      # Python 依赖
```

---

## 🚀 快速开始

### 前置要求

- **Python** 3.11+
- **Node.js** 18+
- **Docker** & **Docker Compose**（用于启动 PostgreSQL、Milvus、Redis）

### 1. 克隆仓库

```bash
git clone https://github.com/<your-username>/CookAgent.git
cd CookAgent
```

### 2. 启动基础设施

```bash
docker compose up -d
```

这将启动：
- PostgreSQL（pgvector 扩展，端口 5432）
- Milvus（向量数据库，端口 19530）
- Redis（缓存，端口 6379）

### 3. 配置环境变量

```bash
cp .env.example .env
# 编辑 .env，填入你的 API Key 和密钥：
#   DASHSCOPE_API_KEY=你的阿里云 DashScope API Key
#   DATABASE_PASSWORD=postgres 密码（默认 cookhero_secret）
#   JWT_SECRET_KEY=用 openssl rand -hex 32 生成
```

### 4. 安装后端依赖

```bash
# 创建虚拟环境
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate

# 安装依赖
pip install -r requirements.txt
```

### 5. 摄取菜谱数据

```bash
python scripts/ingest_data.py
```

该脚本会解析 `data/howtocook/dishes/` 下的 Markdown 菜谱文件，存入 PostgreSQL 并构建 Milvus 向量索引。

### 6. 安装前端依赖

```bash
cd frontend
npm install
```

### 7. 启动服务

**终端 1 — 启动后端：**
```bash
uvicorn app.main:app --reload --port 8000
```

**终端 2 — 启动前端：**
```bash
cd frontend
npm run dev
```

### 8. 访问应用

打开浏览器访问 **http://localhost:5173**

- 注册账号 → 登录 → 切换 RAG / Agent 模式 → 开始对话 🎉

---

## ⚙️ 配置说明

| 配置文件 | 用途 |
|---------|------|
| `config.yml` | 非敏感配置：模型名、检索参数、意图检测、Agent 路由、限流规则、压缩策略 |
| `.env` | 敏感配置：API Key、数据库密码、JWT 密钥 |

`config.yml` 支持 `${VAR}` 占位符，会自动从 `.env` 中读取对应值。

### LLM 两层分级

| 层级 | 默认模型 | 用途 |
|------|---------|------|
| **Fast** | qwen-plus | 意图检测、查询改写、上下文压缩 |
| **Normal** | qwen-max | 最终回答生成、Agent ReAct 推理 |

### Agent 路由（P1）

| 意图类型 | Agent | 说明 |
|---------|-------|------|
| `recipe_search` | RecipeMasterAgent | 菜谱搜索、做法查询、食材替换 |
| `cooking_help` | RecipeMasterAgent | 烹饪技巧、步骤指导 |
| `diet_plan` | DietPlannerAgent | 饮食计划（P2 完整实现） |
| `general_chat` | GeneralAgent | 闲聊兜底 |

---

## 🗺️ 路线图

- [x] **P0** — 核心基础设施：RAG 管线、ReAct Agent、安全防护、用户系统
- [x] **P0** — 前端双模式对话界面（RAG 对话 + Agent 对话）
- [x] **P1** — 意图识别、查询改写、Agent Registry 路由、Reranker 精排、多 Agent 协作
- [x] **P2 设计** — 饮食管理/天气/时令/视觉/MCP/评价系统设计方案（`docs/p2-design-plan.md`）
- [ ] **P2 实施** — 饮食管理模块、工具系统升级、子代理系统、多模态、评价系统（8 轮 35 文件）
- [ ] **P3** — 性能优化、移动端适配、生产部署

---

## 📄 文档索引

| 文档 | 描述 |
|------|------|
| [`docs/p1-system-architecture.md`](docs/p1-system-architecture.md) | P1 系统架构：数据流、ReAct 循环、工具系统、SSE 协议、配置详解 |
| [`docs/p2-design-plan.md`](docs/p2-design-plan.md) | P2 设计方案：饮食模块、工具升级、子代理、MCP、评价系统、前端配置 |

---

## 🤝 致谢

本项目架构设计借鉴了 [**CookHero**](https://github.com/Decade-qiu/CookHero)（[@Decade-qiu](https://github.com/Decade-qiu)），在其基础上进行了全面重构与功能扩展。感谢原作者的优秀工作！

菜谱数据来源于 [**HowToCook**](https://github.com/Anduin2017/HowToCook) 开源项目。

---

## 📄 开源协议

本项目基于 [MIT License](LICENSE) 开源。
