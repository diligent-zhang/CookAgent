# CookAgent P1 阶段完整代码

> 日期: 2026-06-23 | 按顺序实施，每完成一组验证后再继续

---

## 流程架构图

```
                        ┌──────────────────────────────────────────┐
                        │              用户消息                      │
                        │      "帮我找几个快手的减脂菜"               │
                        └──────────────────┬───────────────────────┘
                                           │
                        ┌──────────────────▼───────────────────────┐
                        │         [1] IntentDetector (P1 新增)      │
                        │                                         │
                        │  fast LLM 分类 ──→ Intent                │
                        │  type="recipe_search"                    │
                        │  confidence=0.95                         │
                        │  keywords=["快手","减脂"]                  │
                        │  filters={difficulty:2, tags:["低脂"]}    │
                        │                                         │
                        │  ┌─ Redis 缓存 ─────────────────────┐    │
                        │  │ key: intent:{md5(query)}         │    │
                        │  │ TTL: 3600s                       │    │
                        │  └──────────────────────────────────┘    │
                        │                                         │
                        │  降级: LLM不可用 → 默认 recipe_search      │
                        └──────────────────┬───────────────────────┘
                                           │
                        ┌──────────────────▼───────────────────────┐
                        │         [2] QueryRewriter (P1 新增)       │
                        │                                         │
                        │  仅在 recipe_search / cooking_help 触发   │
                        │                                         │
                        │  "帮我找几个快手的减脂菜"                   │
                        │       ↓ fast LLM 改写                    │
                        │  "快手 减脂 菜谱 低脂 30分钟内"            │
                        │                                         │
                        │  降级: 改写失败 → 使用原查询                │
                        └──────────────────┬───────────────────────┘
                                           │
                        ┌──────────────────▼───────────────────────┐
                        │         [3] AgentRegistry (P1 新增)       │
                        │                                         │
                        │  ┌──────────────────────────────────┐    │
                        │  │ 意图         →    Agent           │    │
                        │  │ recipe_search → RecipeMasterAgent │    │
                        │  │ cooking_help  → GeneralAgent      │    │
                        │  │ ingredient_ask→ GeneralAgent      │    │
                        │  │ diet_plan     → DietPlannerAgent  │    │
                        │  │ general_chat  → GeneralAgent      │    │
                        │  └──────────────────────────────────┘    │
                        │                                         │
                        │  降级: 无匹配 → GeneralAgent (默认)        │
                        └──────────────────┬───────────────────────┘
                                           │
           ┌───────────────────────────────┼───────────────────────────────┐
           │                               │                               │
    ┌──────▼──────┐              ┌────────▼────────┐            ┌─────────▼────────┐
    │ recipe_search│              │ cooking_help     │            │  diet_plan       │
    │              │              │ ingredient_ask   │            │  (P2 骨架)       │
    │              │              │ general_chat     │            │                  │
    └──────┬───────┘              └────────┬─────────┘            └────────┬─────────┘
           │                               │                               │
    ┌──────▼───────┐              ┌────────▼─────────┐            ┌────────▼─────────┐
    │RecipeMaster  │              │  GeneralAgent    │            │ DietPlannerAgent │
    │   Agent      │              │                  │            │  → "即将上线"     │
    │              │              │  简单RAG + LLM    │            │                  │
    │ 增强RAG流程:  │              │  无特殊优化       │            └──────────────────┘
    │              │              └────────┬─────────┘
    │ ① Metadata  │                       │
    │    Filter   │              ┌────────▼─────────┐
    │    (Milvus  │              │    LLM 流式生成   │
    │     expr)   │              │   (qwen-max)     │
    │              │              └──────────────────┘
    │ ② RAG 检索  │
    │   (改写查询) │
    │              │
    │ ③ Reranker  │
    │   精排      │
    │   (gte-     │
    │    rerank)  │
    │              │
    │ ④ LLM 生成  │
    │   (qwen-max) │
    └──────┬───────┘
           │
    ┌──────▼───────────────────────────────────────────────────────┐
    │                    [4] SSE Stream → 前端                       │
    │                                                              │
    │  事件流:                                                     │
    │    intent    → {type:"recipe_search", confidence:0.95}       │
    │    thought   → "正在精准搜索菜谱..."                          │
    │    sources   → [{dish_name:"清蒸鲈鱼", score:0.92}, ...]     │
    │    token     → "推荐以下几道快手减脂菜..."  (逐token)          │
    │    done      → {content:"完整回答文本"}                       │
    └──────────────────────────────────────────────────────────────┘



P1 核心数据流对比:

┌─── P0 (当前) ──────────────────────┐    ┌─── P1 (目标) ──────────────────────────┐
│                                    │    │                                        │
│  用户消息                           │    │  用户消息                               │
│    ↓                               │    │    ↓                                   │
│  固定 RAG 检索                      │    │  IntentDetector → 意图分类              │
│    ↓                               │    │    ↓                                   │
│  固定 LLM 生成 (或 ReAct 循环)       │    │  QueryRewriter → 查询优化               │
│    ↓                               │    │    ↓                                   │
│  SSE 返回                           │    │  AgentRegistry → 动态路由               │
│                                    │    │    ↓                                   │
│  问题:                             │    │  ┌─ recipe_search → RecipeMaster       │
│  1. 所有请求走同一条路径              │    │  │  · MetadataFilter (Milvus expr)     │
│  2. "上次那个排骨"检索效果差          │    │  │  · RAG + Reranker 精排              │
│  3. 新增 Agent 要改大量代码           │    │  │  · 单轮 LLM 生成 (省 2-3 次调用)    │
│  4. 检索结果没有精排                  │    │  ├─ cooking_help → GeneralAgent       │
│                                    │    │  │  · 轻量 RAG + LLM                   │
│                                    │    │  └─ general_chat → GeneralAgent        │
│                                    │    │     · 直接 LLM (无 RAG)                │
│                                    │    │    ↓                                   │
│                                    │    │  SSE 返回                              │
│                                    │    │                                        │
└────────────────────────────────────┘    └────────────────────────────────────────┘
```

---

## 一、新建文件

---

### 1.1 `app/conversation/prompts.py` — LLM 提示词模板

```python
"""
意图分类和查询改写的 LLM 提示词模板。

设计原则：
  1. 用 JSON 格式约束 LLM 输出，方便结构化解码
  2. 提示词明确每种意图的判定标准，减少歧义
  3. 要求 LLM 同时提取关键词和过滤条件，一次调用完成多任务
"""

# ============================================================
# 意图分类 System Prompt
# ============================================================
INTENT_CLASSIFICATION_PROMPT = """你是一个查询意图分类器。分析用户的烹饪相关问题，输出 JSON 格式的分类结果。

## 意图类型

1. **recipe_search** — 用户想查找菜谱
   特征：提到具体菜名、食材、做法、场景
   示例："红烧肉怎么做"、"有什么清淡的菜"、"找几个快手的减脂菜"

2. **cooking_help** — 用户需要烹饪技巧/知识
   特征：问烹饪方法、技巧、原理，不指定具体菜谱
   示例："油温怎么判断"、"炒菜为什么粘锅"、"如何让肉更嫩"

3. **diet_plan** — 用户想制定饮食计划
   特征：涉及多天饮食安排、热量控制、营养搭配
   示例："帮我制定一周减脂食谱"、"我想增肌，每天怎么吃"

4. **ingredient_ask** — 用户询问食材相关问题
   特征：食材搭配、替代、营养、保存
   示例："鸡蛋和番茄能一起吃吗"、"山药怎么削皮不痒"

5. **general_chat** — 闲聊或无法归类
   特征：打招呼、感谢、无关话题
   示例："你好"、"谢谢你的帮助"

## 输出格式

严格返回 JSON，不要输出其他内容：

{
  "intent": "recipe_search",
  "confidence": 0.95,
  "keywords": ["快手", "减脂"],
  "filters": {
    "category": null,
    "difficulty": null,
    "cooking_time_minutes": null,
    "tags": []
  }
}

## 过滤条件提取规则

- **difficulty**: 用户说"简单/新手/快手" → difficulty <= 2；用户说"复杂/大菜/硬菜" → difficulty >= 4
- **category**: 用户说"汤/汤类" → "soup"；"荤菜/肉菜" → "meat_dish"；"素菜/蔬菜" → "vegetable_dish"；"主食/饭/面" → "staple"；"甜点" → "dessert"；"早餐" → "breakfast"；"饮品/饮料" → "drink"
- **cooking_time_minutes**: 用户提到时间如"30分钟"、"半小时" → 对应数值
- **tags**: 提取饮食偏好标签，如"低脂"、"高蛋白"、"素食"、"快手"

## 注意事项

- confidence 表示你对分类的确信度（0.0~1.0）
- 如果用户消息很短或模糊，降低 confidence
- filters 中没提到的字段填 null 或空数组
"""

# ============================================================
# 查询改写 System Prompt
# ============================================================
QUERY_REWRITE_PROMPT = """你是一个查询改写助手。你的任务是把用户的口语化查询改写为更适合搜索引擎检索的关键词。

## 改写目标

1. 去除口语噪音词：去掉"上次那个"、"怎么做来着"、"帮我找一下"等无信息量的词
2. 补全省略：用户说"排骨"可能指"排骨做法"、"排骨菜谱"
3. 展开别名/错别字：如"鱼香rose" → "鱼香肉丝"，"土豆" ↔ "马铃薯"
4. 提取关键特征：用户说"快一点的减脂午餐" → "快手 减脂 午餐 菜谱 30分钟内"
5. 保留约束条件：用户提到的口味、食材、时间等限制必须保留

## 示例

输入：上次那个排骨的怎么做来着
输出：排骨 做法

输入：有没有快手的午餐，最好低脂
输出：快手 午餐 菜谱 低脂 30分钟内

输入：我想做个适合夏天喝的汤
输出：夏季 汤品 清淡 消暑

输入：鱼香rose
输出：鱼香肉丝 做法

## 规则

- 输出纯关键词文本，不要加解释
- 多个关键词用空格分隔
- 不要超过 200 字符
- 如果用户输入已经很简洁，保持原样
"""
```

---

### 1.2 `app/conversation/intent.py` — 意图分类器

```python
"""
意图分类器

使用 fast LLM 将用户输入分类到 5 种意图之一。
支持 Redis 缓存和自动降级。
"""
import hashlib
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Optional

from app.conversation.prompts import INTENT_CLASSIFICATION_PROMPT
from app.llm.provider import LLMProvider

logger = logging.getLogger(__name__)


@dataclass
class IntentFilters:
    """意图分类时提取的过滤条件。"""
    category: Optional[str] = None
    difficulty: Optional[int] = None
    cooking_time_minutes: Optional[int] = None
    tags: list = field(default_factory=list)


@dataclass
class Intent:
    """意图分类结果。"""
    type: str                    # recipe_search | cooking_help | diet_plan | ingredient_ask | general_chat
    confidence: float            # 0.0 ~ 1.0
    keywords: list = field(default_factory=list)
    filters: IntentFilters = field(default_factory=IntentFilters)
    raw_response: str = ""       # LLM 原始返回，调试用


# 意图 → 缓存 key 前缀
INTENT_CACHE_PREFIX = "intent"

# 默认降级意图（LLM 不可用时使用）
FALLBACK_INTENT = "recipe_search"

# LLM 超时时间（秒）
INTENT_TIMEOUT = 2


class IntentDetector:
    """
    意图分类器。

    用法：
        detector = IntentDetector(llm_provider, redis_client=None)
        intent = await detector.detect("帮我找几个快手的减脂菜")
        # → Intent(type="recipe_search", confidence=0.95, keywords=["快手", "减脂"])
    """

    def __init__(
        self,
        llm_provider: LLMProvider,
        redis_client=None,
        cache_ttl: int = 3600,
    ):
        self.llm_provider = llm_provider
        self.redis = redis_client
        self.cache_ttl = cache_ttl

    async def detect(self, query: str) -> Intent:
        """
        检测用户查询的意图。

        流程：
          1. 检查 Redis 缓存（相同 query 不重复检测）
          2. 调用 fast LLM 分类
          3. 写入缓存
          4. 解析失败 → 降级为 recipe_search
        """
        # ==== Step 1: 缓存检查 ====
        cache_key = self._cache_key(query)
        if self.redis:
            try:
                cached = await self.redis.get(cache_key)
                if cached:
                    data = json.loads(cached)
                    return Intent(
                        type=data["type"],
                        confidence=data["confidence"],
                        keywords=data.get("keywords", []),
                        filters=IntentFilters(**data.get("filters", {})),
                        raw_response="(cached)",
                    )
            except Exception:
                pass  # Redis 出错不影响主流程

        # ==== Step 2: LLM 分类 ====
        try:
            raw_result = await self._call_llm(query)
        except Exception as e:
            logger.warning("Intent detection LLM call failed: %s", e)
            return self._fallback(query)

        # ==== Step 3: 解析 JSON ====
        intent = self._parse_result(raw_result, query)

        # ==== Step 4: 写入缓存 ====
        if self.redis:
            try:
                cache_data = json.dumps({
                    "type": intent.type,
                    "confidence": intent.confidence,
                    "keywords": intent.keywords,
                    "filters": {
                        "category": intent.filters.category,
                        "difficulty": intent.filters.difficulty,
                        "cooking_time_minutes": intent.filters.cooking_time_minutes,
                        "tags": intent.filters.tags,
                    },
                }, ensure_ascii=False)
                await self.redis.setex(cache_key, self.cache_ttl, cache_data)
            except Exception:
                pass

        return intent

    # ===== 私有方法 =====

    def _cache_key(self, query: str) -> str:
        """生成缓存键：intent:{md5(query)}"""
        digest = hashlib.md5(query.encode("utf-8")).hexdigest()
        return f"{INTENT_CACHE_PREFIX}:{digest}"

    async def _call_llm(self, query: str) -> str:
        """调用 fast LLM 获取分类结果。"""
        from langchain_core.messages import HumanMessage, SystemMessage

        invoker = self.llm_provider.create_invoker("fast", streaming=False)
        messages = [
            SystemMessage(content=INTENT_CLASSIFICATION_PROMPT),
            HumanMessage(content=query),
        ]

        start = time.time()
        response = await invoker.ainvoke(messages)
        elapsed = time.time() - start

        if hasattr(response, "content"):
            content = response.content
        else:
            content = str(response)

        logger.info("Intent detection took %.2fs for query='%s...'", elapsed, query[:30])
        return content

    def _parse_result(self, raw: str, query: str) -> Intent:
        """解析 LLM 返回的 JSON，失败时降级。"""
        try:
            json_str = raw.strip()
            if json_str.startswith("```"):
                lines = json_str.split("\n")
                json_str = "\n".join(lines[1:-1])
                json_str = json_str.strip()

            data = json.loads(json_str)

            intent_type = data.get("intent", FALLBACK_INTENT)
            valid_intents = {
                "recipe_search", "cooking_help", "diet_plan",
                "ingredient_ask", "general_chat",
            }
            if intent_type not in valid_intents:
                intent_type = FALLBACK_INTENT

            confidence = float(data.get("confidence", 0.5))
            keywords = data.get("keywords", [])

            filters_data = data.get("filters", {})
            filters = IntentFilters(
                category=filters_data.get("category"),
                difficulty=filters_data.get("difficulty"),
                cooking_time_minutes=filters_data.get("cooking_time_minutes"),
                tags=filters_data.get("tags", []),
            )

            return Intent(
                type=intent_type,
                confidence=min(max(confidence, 0.0), 1.0),
                keywords=keywords,
                filters=filters,
                raw_response=raw,
            )
        except (json.JSONDecodeError, KeyError, ValueError) as e:
            logger.warning(
                "Failed to parse intent JSON for '%s...': %s, raw=%s",
                query[:30], e, raw[:200],
            )
            return self._fallback(query)

    def _fallback(self, query: str) -> Intent:
        """降级策略：返回默认意图 recipe_search。"""
        return Intent(
            type=FALLBACK_INTENT,
            confidence=0.3,
            keywords=[query],
            filters=IntentFilters(),
            raw_response="(fallback)",
        )
```

---

### 1.3 `app/conversation/query_rewriter.py` — 查询改写器

```python
"""
查询改写器

在 RAG 检索之前优化用户查询，提升检索命中率。
使用 fast LLM 做改写，仅对特定意图触发。
"""
import logging
from typing import Optional

from app.conversation.prompts import QUERY_REWRITE_PROMPT
from app.llm.provider import LLMProvider

logger = logging.getLogger(__name__)

# 需要改写的意图类型
REWRITE_INTENTS = {"recipe_search", "cooking_help"}


class QueryRewriter:
    """
    查询改写器。

    用法：
        rewriter = QueryRewriter(llm_provider)
        rewritten = await rewriter.rewrite(
            query="上次那个排骨怎么做来着",
            intent_type="recipe_search",
        )
        # → "排骨 做法"
    """

    def __init__(
        self,
        llm_provider: LLMProvider,
        enabled: bool = True,
        max_length: int = 200,
    ):
        self.llm_provider = llm_provider
        self.enabled = enabled
        self.max_length = max_length

    async def rewrite(self, query: str, intent_type: str) -> str:
        """
        改写用户查询。

        只在以下条件全部满足时才改写：
          1. enabled = True
          2. 意图类型在 REWRITE_INTENTS 中
          3. 查询文本已经有足够长度（太短不需要改写）

        返回改写后的查询，出错时返回原查询。
        """
        if not self.enabled:
            return query

        if intent_type not in REWRITE_INTENTS:
            return query

        if len(query) < 10:
            return query

        try:
            rewritten = await self._call_llm(query)
            if rewritten and len(rewritten) <= self.max_length:
                logger.info(
                    "Query rewritten: '%s...' → '%s...'",
                    query[:30], rewritten[:30],
                )
                return rewritten
            else:
                return query
        except Exception as e:
            logger.warning("Query rewriting failed: %s, using original", e)
            return query

    async def _call_llm(self, query: str) -> str:
        """调用 fast LLM 改写查询。"""
        from langchain_core.messages import HumanMessage, SystemMessage

        invoker = self.llm_provider.create_invoker("fast", streaming=False)
        messages = [
            SystemMessage(content=QUERY_REWRITE_PROMPT),
            HumanMessage(content=query),
        ]

        response = await invoker.ainvoke(messages)

        if hasattr(response, "content"):
            return response.content.strip()
        return str(response).strip()
```

---

### 1.4 `app/agent/agents/base.py` — Agent 抽象基类

```python
"""
Agent 抽象基类

定义所有 Agent 必须遵守的接口契约。
引入此基类后，调用方只依赖抽象而不依赖具体实现，
新增 Agent 类型只需实现 execute() 方法并注册即可。
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from langchain_core.documents import Document


# ============================================================
# 上下文和结果数据结构
# ============================================================

@dataclass
class AgentContext:
    """传给 Agent 的上下文信息。"""
    user_id: str
    session_id: str
    intent_type: str                                    # 意图类型
    intent_confidence: float = 1.0                      # 意图置信度
    rewritten_query: Optional[str] = None               # 改写后的查询
    original_query: str = ""                            # 用户原始查询
    compressed_summary: Optional[str] = None            # 历史对话摘要
    recent_messages: List[Any] = field(default_factory=list)  # 最近 K 轮原文
    user_preferences: Dict[str, Any] = field(default_factory=dict)


@dataclass
class Source:
    """引用的数据源。"""
    dish_name: str
    category: str = ""
    relevance_score: float = 0.0
    source_path: str = ""


@dataclass
class AgentResult:
    """Agent 执行结果。"""
    content: str                                        # 最终回答文本
    tool_calls_made: int = 0                            # 工具调用次数
    sources: List[Source] = field(default_factory=list) # 引用的数据源
    metadata: Dict[str, Any] = field(default_factory=dict)


# ============================================================
# 抽象基类
# ============================================================

class BaseAgent(ABC):
    """
    所有 Agent 的基类。

    子类必须实现 execute() 方法。
    """

    name: str = ""                    # 唯一标识符，用于注册
    description: str = ""             # 用途说明
    intent_match: List[str] = []      # 匹配哪些意图类型

    @abstractmethod
    async def execute(
        self,
        context: AgentContext,
        stream_callback: Optional[Callable] = None,
    ) -> AgentResult:
        """
        执行 Agent 逻辑。

        参数：
            context: 包含用户消息、意图、历史等上下文信息
            stream_callback: SSE 回调，签名为 async def callback(event_type: str, data: dict)

        返回：
            AgentResult 包含最终回答和相关元数据
        """
        ...

    def can_handle(self, intent_type: str) -> bool:
        """判断此 Agent 是否能处理给定意图。"""
        return intent_type in self.intent_match

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(name={self.name})"
```

---

### 1.5 `app/agent/registry/hub.py` — Agent 注册中心

```python
"""
Agent 注册中心

管理所有 Agent 的注册、查找和意图路由。
使用 Registry Pattern，新增 Agent 只需注册，无需修改调度代码。
"""
import logging
from typing import Dict, List, Optional, Type

from app.agent.agents.base import BaseAgent

logger = logging.getLogger(__name__)


class AgentRegistry:
    """
    Agent 注册中心。

    用法：
        registry = AgentRegistry()
        registry.register(RecipeMasterAgent(llm, rag))
        registry.register(GeneralAgent(llm, rag))
        registry.set_default("general")

        agent = registry.match("recipe_search")
        result = await agent.execute(context, callback)
    """

    def __init__(self):
        self._agents: Dict[str, BaseAgent] = {}
        self._intent_routing: Dict[str, str] = {}
        self._default_name: Optional[str] = None

    def register(self, agent: BaseAgent) -> None:
        """注册一个 Agent 实例，自动建立意图路由映射。"""
        if not agent.name:
            raise ValueError("Agent name cannot be empty")

        self._agents[agent.name] = agent

        for intent_type in agent.intent_match:
            if intent_type in self._intent_routing:
                logger.warning(
                    "Intent '%s' was handled by '%s', now overridden by '%s'",
                    intent_type, self._intent_routing[intent_type], agent.name,
                )
            self._intent_routing[intent_type] = agent.name

        logger.info(
            "Registered agent '%s' for intents: %s",
            agent.name, agent.intent_match,
        )

    def set_default(self, agent_name: str) -> None:
        """设置默认 Agent（无匹配意图时使用）。"""
        if agent_name not in self._agents:
            raise ValueError(f"Agent '{agent_name}' not registered")
        self._default_name = agent_name

    def get(self, name: str) -> Optional[BaseAgent]:
        """按名称获取 Agent。"""
        return self._agents.get(name)

    def match(self, intent_type: str) -> BaseAgent:
        """根据意图类型匹配合适的 Agent。"""
        agent_name = self._intent_routing.get(intent_type)
        if agent_name:
            agent = self._agents.get(agent_name)
            if agent:
                return agent

        if self._default_name:
            logger.info(
                "No agent for intent '%s', falling back to default '%s'",
                intent_type, self._default_name,
            )
            return self._agents[self._default_name]

        raise ValueError(
            f"No agent registered for intent '{intent_type}' and no default set"
        )

    def list(self) -> List[str]:
        """列出所有已注册 Agent 的名称。"""
        return list(self._agents.keys())

    def list_routes(self) -> Dict[str, str]:
        """列出意图 → Agent 的路由表。"""
        return dict(self._intent_routing)


# 全局单例
_global_registry: Optional[AgentRegistry] = None


def get_agent_registry() -> AgentRegistry:
    """获取全局 AgentRegistry 单例。"""
    global _global_registry
    if _global_registry is None:
        _global_registry = AgentRegistry()
    return _global_registry


def reset_agent_registry() -> None:
    """重置全局注册中心（测试用）。"""
    global _global_registry
    _global_registry = AgentRegistry()
```

---

### 1.6 `app/agent/agents/default.py` — 通用对话 Agent

```python
"""
通用 Agent

处理 general_chat、cooking_help、ingredient_ask 等非菜谱检索意图。
核心流程：简单 RAG 检索 + LLM 流式生成。
"""
import logging
from typing import Callable, Optional

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from app.agent.agents.base import AgentContext, AgentResult, BaseAgent, Source
from app.llm.provider import LLMProvider
from app.rag.service import RAGService

logger = logging.getLogger(__name__)

GENERAL_AGENT_PROMPT = """你是 CookHero，一个专业的烹饪助手。你的职责是帮助用户解决烹饪相关的问题。

## 能力范围
- 菜谱搜索和推荐
- 烹饪技巧和知识解答
- 食材信息查询（营养、搭配、保存、替代）
- 饮食建议

## 回答规则
1. 如果提供了菜谱资料，基于资料回答；资料中不包含的，诚实说明并给出通用建议
2. 回答清晰、步骤化，菜谱步骤按序号列出
3. 主动提示烹饪技巧、注意事项
4. 如果用户有饮食限制，优先考虑约束
5. 用中文回答，语气友好热情

你是用户厨房里的可靠伙伴！
"""


class GeneralAgent(BaseAgent):
    """
    通用对话 Agent。

    处理意图：general_chat, cooking_help, ingredient_ask
    """

    name = "general"
    description = "通用对话 Agent，处理烹饪技巧、食材问答和闲聊"
    intent_match = ["general_chat", "cooking_help", "ingredient_ask"]

    def __init__(self, llm_provider: LLMProvider, rag_service: RAGService):
        self.llm_provider = llm_provider
        self.rag_service = rag_service

    async def execute(
        self,
        context: AgentContext,
        stream_callback: Optional[Callable] = None,
    ) -> AgentResult:
        query = context.rewritten_query or context.original_query

        # ==== RAG 检索 ====
        if stream_callback:
            await stream_callback("thinking", {"content": "正在检索相关资料..."})

        try:
            retrieved_docs = await self.rag_service.retrieve(
                query=query, user_id=context.user_id,
            )
        except Exception as e:
            logger.error("RAG retrieval failed: %s", e)
            retrieved_docs = []

        # ==== 构建消息 ====
        messages = self._build_messages(
            query=query,
            retrieved_docs=retrieved_docs,
            compressed_summary=context.compressed_summary,
            recent_messages=context.recent_messages,
        )

        # ==== 发送 sources ====
        if stream_callback:
            await stream_callback("sources", {
                "sources": self._build_sources(retrieved_docs),
            })

        # ==== 流式生成 ====
        invoker = self.llm_provider.create_invoker("normal", streaming=True)
        full_response = ""

        try:
            async for chunk in invoker.astream(messages):
                token = self._extract_text(chunk)
                if token:
                    full_response += token
                    if stream_callback:
                        await stream_callback("token", {"content": token})
        except Exception as e:
            logger.error("LLM streaming failed: %s", e)
            error_msg = f"生成回答失败: {e}"
            if stream_callback:
                await stream_callback("error", {"content": error_msg})
            return AgentResult(content=error_msg)

        return AgentResult(
            content=full_response,
            sources=self._build_sources(retrieved_docs),
        )

    def _build_messages(self, query, retrieved_docs, compressed_summary, recent_messages):
        messages = [SystemMessage(content=GENERAL_AGENT_PROMPT)]

        if compressed_summary:
            messages.append(SystemMessage(content=f"对话历史摘要：{compressed_summary}"))

        for msg in recent_messages:
            if hasattr(msg, "role") and hasattr(msg, "content"):
                if msg.role == "user":
                    messages.append(HumanMessage(content=msg.content))
                elif msg.role == "assistant":
                    messages.append(AIMessage(content=msg.content))

        if retrieved_docs:
            parts = ["以下是从菜谱库中检索到的相关资料:\n"]
            for i, doc in enumerate(retrieved_docs, 1):
                name = doc.metadata.get("dish_name", "未知")
                parts.append(f"--- 资料{i}: {name} ---\n{doc.page_content}\n")
            messages.append(HumanMessage(
                content="\n".join(parts) + "\n\n请根据以上资料回答用户的问题。"
            ))

        messages.append(HumanMessage(content=query))
        return messages

    def _build_sources(self, docs):
        return [
            {
                "dish_name": doc.metadata.get("dish_name", "未知"),
                "category": doc.metadata.get("category", ""),
                "relevance_score": doc.metadata.get("retrieval_score", 0.0),
            }
            for doc in docs
        ]

    def _extract_text(self, chunk) -> str:
        if hasattr(chunk, "content") and isinstance(chunk.content, str):
            return chunk.content
        if hasattr(chunk, "message") and hasattr(chunk.message, "content"):
            return chunk.message.content or ""
        return ""
```

---

### 1.7 `app/agent/subagents/builtin/recipe_master.py` — 菜谱专家 Agent

```python
"""
菜谱专家 Agent

处理 recipe_search 意图。
核心流程：增强 RAG（元数据过滤 + 精排） → LLM 流式生成。

与 ReActAgent 的区别：
  ReActAgent：用户问 → 思考 → 调工具 → 观察 → 再思考 → 回答（多轮 LLM 调用）
  本 Agent： 用户问 → RAG 检索 → Rerank → LLM 生成（单轮 LLM 调用）

对于菜谱搜索场景，单轮模式更高效：不需要工具调用来回对话，
直接检索 + 生成即可完成绝大多数菜谱查询。
"""
import logging
from typing import Callable, Optional

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from app.agent.agents.base import AgentContext, AgentResult, BaseAgent, Source
from app.llm.provider import LLMProvider
from app.rag.service import RAGService

logger = logging.getLogger(__name__)

RECIPE_MASTER_PROMPT = """你是 CookHero 菜谱专家，专门帮助用户查找和推荐菜谱。

## 你的专长
- 根据用户口味、食材、场景推荐合适的菜谱
- 提供详细的烹饪步骤和技巧
- 根据用户的饮食限制（过敏、素食等）调整推荐
- 比较不同做法，推荐最优方案

## 回答格式
1. 先列出推荐的菜谱（含难度、时间、口味）
2. 对每个菜谱给出简要说明
3. 重点推荐的那道菜给出完整步骤
4. 附上烹饪小贴士

## 注意事项
- 如果用户有过敏食材，务必排除含该食材的菜谱
- 如果搜索结果不理想，诚实告知并给出替代方向
- 用中文回答，语气专业但亲切
"""


class RecipeMasterAgent(BaseAgent):
    """
    菜谱专家 Agent。

    处理意图：recipe_search
    """

    name = "recipe_master"
    description = "菜谱专家 Agent，负责菜谱搜索、推荐和详细指导"
    intent_match = ["recipe_search"]

    def __init__(self, llm_provider: LLMProvider, rag_service: RAGService, reranker=None):
        self.llm_provider = llm_provider
        self.rag_service = rag_service
        self.reranker = reranker

    async def execute(
        self,
        context: AgentContext,
        stream_callback: Optional[Callable] = None,
    ) -> AgentResult:
        query = context.rewritten_query or context.original_query

        # ==== RAG 检索 ====
        if stream_callback:
            await stream_callback("thinking", {"content": "正在精准搜索菜谱..."})

        try:
            retrieved_docs = await self.rag_service.retrieve(
                query=query, user_id=context.user_id,
            )
        except Exception as e:
            logger.error("RAG retrieval failed: %s", e)
            retrieved_docs = []

        # ==== Reranker 精排 ====
        if self.reranker and retrieved_docs:
            if stream_callback:
                await stream_callback("thinking", {"content": "正在优化搜索结果排序..."})
            try:
                retrieved_docs = await self.reranker.rerank(
                    query=query, documents=retrieved_docs,
                )
            except Exception as e:
                logger.warning("Reranking failed: %s, using raw results", e)

        # ==== 构建消息 ====
        messages = self._build_messages(
            query=context.original_query,
            retrieved_docs=retrieved_docs,
            compressed_summary=context.compressed_summary,
            recent_messages=context.recent_messages,
        )

        # ==== 发送 sources ====
        if stream_callback:
            await stream_callback("sources", {
                "sources": self._build_sources(retrieved_docs),
            })

        # ==== 流式生成 ====
        invoker = self.llm_provider.create_invoker("normal", streaming=True)
        full_response = ""

        try:
            async for chunk in invoker.astream(messages):
                token = self._extract_text(chunk)
                if token:
                    full_response += token
                    if stream_callback:
                        await stream_callback("token", {"content": token})
        except Exception as e:
            logger.error("LLM streaming failed: %s", e)
            error_msg = f"生成回答失败: {e}"
            if stream_callback:
                await stream_callback("error", {"content": error_msg})
            return AgentResult(content=error_msg)

        return AgentResult(
            content=full_response,
            sources=self._build_sources(retrieved_docs),
            metadata={"intent": "recipe_search", "docs_retrieved": len(retrieved_docs)},
        )

    def _build_messages(self, query, retrieved_docs, compressed_summary, recent_messages):
        messages = [SystemMessage(content=RECIPE_MASTER_PROMPT)]

        if compressed_summary:
            messages.append(SystemMessage(content=f"对话历史摘要：{compressed_summary}"))

        for msg in recent_messages:
            if hasattr(msg, "role") and hasattr(msg, "content"):
                if msg.role == "user":
                    messages.append(HumanMessage(content=msg.content))
                elif msg.role == "assistant":
                    messages.append(AIMessage(content=msg.content))

        if retrieved_docs:
            parts = ["以下是从菜谱库中检索到的相关资料:\n"]
            for i, doc in enumerate(retrieved_docs, 1):
                name = doc.metadata.get("dish_name", "未知")
                diff = doc.metadata.get("difficulty", "?")
                cat = doc.metadata.get("category", "?")
                score = doc.metadata.get("retrieval_score", 0.0)
                parts.append(
                    f"--- 菜谱{i}: {name} (难度:{diff} 分类:{cat} 相关度:{score:.2f}) ---\n"
                    f"{doc.page_content}\n"
                )
            messages.append(HumanMessage(
                content="\n".join(parts) + "\n\n请基于以上菜谱资料回答用户的问题。"
            ))

        messages.append(HumanMessage(content=query))
        return messages

    def _build_sources(self, docs):
        return [
            {
                "dish_name": doc.metadata.get("dish_name", "未知"),
                "category": doc.metadata.get("category", ""),
                "difficulty": doc.metadata.get("difficulty", ""),
                "relevance_score": doc.metadata.get("retrieval_score", 0.0),
            }
            for doc in docs
        ]

    def _extract_text(self, chunk) -> str:
        if hasattr(chunk, "content") and isinstance(chunk.content, str):
            return chunk.content
        if hasattr(chunk, "message") and hasattr(chunk.message, "content"):
            return chunk.message.content or ""
        return ""
```

---

### 1.8 `app/agent/subagents/builtin/diet_planner.py` — 饮食规划 Agent 骨架

```python
"""
饮食规划 Agent（P2 骨架）

P1 阶段只定义接口和骨架方法，实际功能在 P2 实现。
"""
import logging
from typing import Callable, Optional

from app.agent.agents.base import AgentContext, AgentResult, BaseAgent
from app.llm.provider import LLMProvider
from app.rag.service import RAGService

logger = logging.getLogger(__name__)


class DietPlannerAgent(BaseAgent):
    """
    饮食规划 Agent。

    处理意图：diet_plan
    P1 阶段为骨架，P2 实现完整的饮食计划生成、日志解析、营养分析。
    """

    name = "diet_planner"
    description = "饮食规划 Agent，负责制定饮食计划、解析饮食日志、营养分析"
    intent_match = ["diet_plan"]

    def __init__(self, llm_provider: LLMProvider, rag_service: RAGService):
        self.llm_provider = llm_provider
        self.rag_service = rag_service

    async def execute(
        self,
        context: AgentContext,
        stream_callback: Optional[Callable] = None,
    ) -> AgentResult:
        logger.info("DietPlannerAgent called (skeleton mode)")

        msg = (
            "饮食规划功能正在开发中，即将上线！\n"
            "届时你可以：\n"
            "- 制定一周减肥/增肌食谱\n"
            "- 记录每日饮食并自动计算热量\n"
            "- 获取营养分析报告和改进建议\n\n"
            "现在你可以先试试菜谱搜索功能哦~"
        )

        if stream_callback:
            await stream_callback("token", {"content": msg})

        return AgentResult(content=msg, metadata={"mode": "skeleton"})
```

---

### 1.9 `app/rag/rerankers/base.py` — Reranker 抽象基类

```python
"""
Reranker 抽象基类

定义重排序器的统一接口。
"""
from abc import ABC, abstractmethod
from typing import List

from langchain_core.documents import Document


class BaseReranker(ABC):
    """
    重排序器基类。

    用 query 和每个 document 做深度语义匹配，重新计算相关性分数并排序。

    为什么需要 Reranker？
      Milvus 混合检索是粗排——基于向量距离和 BM25 打分，关注"长得像"。
      Reranker 是精排——基于 Cross-Encoder 深度语义匹配，关注"真正相关"。

      案例：
        查询："有什么菜适合老人吃"
        粗排 top-1: "老人头菌炒肉" ← BM25 匹配了"老人"字面，但这是菌类菜谱
        精排后降权：真正的老人适合菜谱（软烂易消化）被提升到前面
    """

    @abstractmethod
    async def rerank(
        self,
        query: str,
        documents: List[Document],
        top_n: int = 5,
    ) -> List[Document]:
        """
        重排序文档列表。

        参数：
            query: 用户查询
            documents: 候选文档列表（通常 top_k=9）
            top_n: 返回的文档数量（通常 5）

        返回：
            按相关性重新排序的文档列表
        """
        ...
```

---

### 1.10 `app/rag/rerankers/dashscope_reranker.py` — DashScope 重排序实现

```python
"""
DashScope Reranker

使用阿里云 DashScope 的 gte-rerank 模型进行检索结果精排。
"""
import logging
from typing import List

import httpx
from langchain_core.documents import Document

from app.config import settings
from app.rag.rerankers.base import BaseReranker

logger = logging.getLogger(__name__)

DASHSCOPE_RERANK_URL = (
    "https://dashscope.aliyuncs.com/api/v1/services/rerank/text-rerank/text-rerank"
)


class DashScopeReranker(BaseReranker):
    """
    DashScope 重排序器。

    使用 gte-rerank 模型对候选文档进行 Cross-Encoder 精排。
    """

    def __init__(
        self,
        api_key: str = "",
        model: str = "gte-rerank",
        top_n: int = 5,
        timeout: float = 10.0,
    ):
        self.api_key = api_key or settings.llm_api_key
        self.model = model
        self.top_n = top_n
        self.timeout = timeout

    async def rerank(
        self,
        query: str,
        documents: List[Document],
        top_n: int = 5,
    ) -> List[Document]:
        if not documents:
            return documents

        if top_n > len(documents):
            top_n = len(documents)

        doc_texts = [doc.page_content for doc in documents]

        payload = {
            "model": self.model,
            "input": {"query": query, "documents": doc_texts},
            "parameters": {"top_n": top_n, "return_documents": False},
        }

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(
                    DASHSCOPE_RERANK_URL, json=payload, headers=headers,
                )
                response.raise_for_status()
                result = response.json()
        except httpx.TimeoutException:
            logger.warning("DashScope Reranker timeout (%.1fs)", self.timeout)
            return documents[:top_n]
        except Exception as e:
            logger.error("DashScope Reranker API failed: %s", e)
            return documents[:top_n]

        try:
            ranked_indices = []
            ranked_scores = []
            for item in result.get("output", {}).get("results", []):
                idx = item.get("index")
                score = item.get("relevance_score", 0.0)
                if idx is not None and 0 <= idx < len(documents):
                    ranked_indices.append(idx)
                    ranked_scores.append(score)

            reranked = []
            for idx, score in zip(ranked_indices, ranked_scores):
                doc = documents[idx]
                doc.metadata["rerank_score"] = float(score)
                doc.metadata["retrieval_score"] = float(score)
                reranked.append(doc)

            logger.info(
                "Reranker: %d docs → %d docs (top_n=%d)",
                len(documents), len(reranked), top_n,
            )
            return reranked[:top_n]

        except Exception as e:
            logger.warning("Failed to parse Reranker response: %s", e)
            return documents[:top_n]
```

---

## 二、需要修改的现有文件

---

### 2.1 `config.yml` — 追加 P1 配置段

在现有 `config.yml` 的 `context:` 段之后追加：

```yaml
# --- 9. 意图识别与查询改写 ---
conversation:
  intent_detection:
    enabled: true
    cache_ttl: 3600
    fallback_intent: "recipe_search"
    timeout: 2

  query_rewriting:
    enabled: true
    max_rewrite_length: 200

# --- 10. Agent 配置 ---
agent:
  enabled_agents:
    - "recipe_master"
    - "general"
    # - "diet_planner"            # P2 启用

# --- 11. RAG 增强配置 ---
rag:
  reranker:
    enabled: true
    model: "gte-rerank"
    top_n: 5
  metadata_filter:
    enabled: true
```

---

### 2.2 `app/config/config.py` — 追加 P1 配置加载

在 `Settings.__init__` 方法的 `self.prompt_guard = ...` 之后，`self.JWT_SECRET_KEY = ...` 之前插入：

```python
        # ===== P1 新增：意图识别与查询改写配置 =====
        conv_raw = raw.get("conversation", {})
        intent_raw = conv_raw.get("intent_detection", {})
        self.intent_detection_enabled: bool = intent_raw.get("enabled", True)
        self.intent_cache_ttl: int = intent_raw.get("cache_ttl", 3600)
        self.intent_fallback: str = intent_raw.get("fallback_intent", "recipe_search")
        self.intent_timeout: int = intent_raw.get("timeout", 2)

        rewrite_raw = conv_raw.get("query_rewriting", {})
        self.query_rewriting_enabled: bool = rewrite_raw.get("enabled", True)
        self.query_rewrite_max_length: int = rewrite_raw.get("max_rewrite_length", 200)

        # ===== P1 新增：Agent 配置 =====
        agent_raw = raw.get("agent", {})
        self.enabled_agents: list = agent_raw.get(
            "enabled_agents", ["recipe_master", "general"]
        )

        # ===== P1 新增：Reranker 配置 =====
        rag_raw = raw.get("rag", {})
        reranker_raw = rag_raw.get("reranker", {})
        self.reranker_enabled: bool = reranker_raw.get("enabled", True)
        self.reranker_model: str = reranker_raw.get("model", "gte-rerank")
        self.reranker_top_n: int = reranker_raw.get("top_n", 5)

        metadata_filter_raw = rag_raw.get("metadata_filter", {})
        self.metadata_filter_enabled: bool = metadata_filter_raw.get("enabled", True)
```

---

### 2.3 `app/main.py` — 修改 init_agent_module 调用

```python
# 原来：
# init_agent_module(llm_provider, rag_service)

# 改为：
init_agent_module(llm_provider, rag_service, redis_client)
```

---

### 2.4 `app/agent/router.py` — 改为注册式初始化

`init_agent_module` 函数完整替换为：

```python
# 在 router.py 顶部追加导入：
# （保留原有导入，追加以下内容）

_redis_client = None


def init_agent_module(llm_provider, rag_service, redis_client=None):
    """初始化 Agent 模块：注入全局服务 + 注册所有 Agent 到 Registry。"""
    global _llm_provider, _rag_service, _redis_client
    _llm_provider = llm_provider
    _rag_service = rag_service
    _redis_client = redis_client

    import logging
    logger = logging.getLogger(__name__)

    from app.agent.registry.hub import get_agent_registry
    from app.agent.agents.default import GeneralAgent
    from app.agent.subagents.builtin.recipe_master import RecipeMasterAgent
    from app.agent.subagents.builtin.diet_planner import DietPlannerAgent
    from app.config import settings

    registry = get_agent_registry()

    # 初始化 Reranker（如果启用）
    reranker = None
    if settings.reranker_enabled:
        try:
            from app.rag.rerankers.dashscope_reranker import DashScopeReranker
            reranker = DashScopeReranker(
                model=settings.reranker_model,
                top_n=settings.reranker_top_n,
            )
            logger.info("Reranker initialized: model=%s", settings.reranker_model)
        except Exception as e:
            logger.warning("Failed to init Reranker, continuing without: %s", e)

    # 创建 Agent 实例
    general_agent = GeneralAgent(llm_provider, rag_service)
    recipe_master = RecipeMasterAgent(llm_provider, rag_service, reranker=reranker)
    diet_planner = DietPlannerAgent(llm_provider, rag_service)

    # 注册（只注册 config 中启用的）
    enabled = settings.enabled_agents
    if "general" in enabled:
        registry.register(general_agent)
        registry.set_default("general")
    if "recipe_master" in enabled:
        registry.register(recipe_master)
    if "diet_planner" in enabled:
        registry.register(diet_planner)

    logger.info("Agent registry initialized with: %s", registry.list())
```

---

### 2.5 `app/agent/service.py` — 核心重构：意图路由

`AgentService` 类的完整替换版（只列出变更的方法，CRUD 方法不变）：

```python
"""
Agent 服务（P1 重构版）

编排 Agent 对话的完整生命周期：
1. 意图识别 → 选择 Agent
2. 查询改写 → 提升检索质量
3. 构建 AgentContext → 统一上下文传递
4. 委托 Agent 执行 → 流式返回 SSE
"""
import logging
from typing import Any, AsyncIterator, Dict, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.agents.base import AgentContext
from app.agent.registry.hub import get_agent_registry
from app.agent.session_repo import AgentSessionRepository
from app.agent.tools import inject_rag_service
from app.conversation.intent import IntentDetector
from app.conversation.query_rewriter import QueryRewriter
from app.config import settings
from app.llm.provider import LLMProvider
from app.rag.service import RAGService

logger = logging.getLogger(__name__)


def _orm_to_dict(obj) -> dict:
    return {c.name: getattr(obj, c.name) for c in obj.__table__.columns}


class AgentService:
    """
    Agent 服务（P1 重构版）。

    核心变化：
      P0: 硬编码创建 ReActAgent，所有请求走同一路径
      P1: 意图识别 → AgentRegistry 路由 → 选择合适的 Agent 执行
    """

    def __init__(
        self,
        db: AsyncSession,
        llm_provider: LLMProvider,
        rag_service: RAGService,
    ):
        self.db = db
        self.llm_provider = llm_provider
        self.rag_service = rag_service
        self.repo = AgentSessionRepository(db)
        inject_rag_service(rag_service)

        # P1 组件
        self.intent_detector = IntentDetector(
            llm_provider=llm_provider,
            redis_client=None,
            cache_ttl=settings.intent_cache_ttl,
        )
        self.query_rewriter = QueryRewriter(
            llm_provider=llm_provider,
            enabled=settings.query_rewriting_enabled,
            max_length=settings.query_rewrite_max_length,
        )

    # ===== CRUD 方法（不变，省略）=====
    # create_session, list_sessions, get_session_detail, delete_session
    # 保持原有实现不变

    # ===== 核心：流式 Agent 聊天（P1 重构）=====

    async def stream_agent_chat(
        self,
        user_id: str,
        session_id: str,
        user_message: str,
    ) -> AsyncIterator[Dict[str, Any]]:
        """
        P1 版 Agent 聊天流程：

          1. 保存用户消息
          2. 意图识别（IntentDetector）
          3. 查询改写（QueryRewriter）
          4. 构建 AgentContext
          5. AgentRegistry 路由 → 选择 Agent
          6. Agent.execute() → SSE 流式返回
          7. 保存最终回答
        """
        # ==== Step 1: 保存用户消息 ====
        await self.repo.add_message(
            session_id=session_id, role="user",
            content=user_message, step_number=0,
        )

        # ==== Step 2: 意图识别 ====
        yield {"type": "thought", "content": "正在理解你的需求..."}
        try:
            intent = await self.intent_detector.detect(user_message)
        except Exception as e:
            logger.warning("Intent detection failed: %s, using fallback", e)
            from app.conversation.intent import Intent, IntentFilters
            intent = Intent(
                type=settings.intent_fallback,
                confidence=0.3,
                keywords=[user_message],
                filters=IntentFilters(),
            )
        yield {"type": "intent", "intent_type": intent.type, "confidence": intent.confidence}

        # ==== Step 3: 查询改写 ====
        rewritten_query = user_message
        if settings.query_rewriting_enabled:
            try:
                rewritten_query = await self.query_rewriter.rewrite(
                    query=user_message, intent_type=intent.type,
                )
            except Exception as e:
                logger.warning("Query rewriting failed: %s", e)

        if rewritten_query != user_message:
            yield {"type": "thought", "content": f"优化查询: {rewritten_query}"}

        # ==== Step 4: 构建 AgentContext ====
        history_msgs = await self.repo.get_last_n_messages(session_id, 20)

        context = AgentContext(
            user_id=user_id,
            session_id=session_id,
            intent_type=intent.type,
            intent_confidence=intent.confidence,
            rewritten_query=rewritten_query,
            original_query=user_message,
            compressed_summary=None,
            recent_messages=history_msgs,
        )

        # ==== Step 5: Agent 路由 ====
        try:
            registry = get_agent_registry()
            agent = registry.match(intent.type)
            logger.info("Routed intent='%s' → agent='%s'", intent.type, agent.name)
        except Exception as e:
            logger.error("Agent routing failed: %s", e)
            yield {"type": "error", "content": f"Agent 路由失败: {e}"}
            return

        # ==== Step 6: 执行 Agent ====
        # 用列表收集流式事件，然后逐条 yield
        collected_events = []

        async def collect_events(event_type: str, data: dict):
            collected_events.append({"type": event_type, **data})

        try:
            result = await agent.execute(context, stream_callback=collect_events)

            # 如果 Agent 没有流式输出 token，直接返回完整结果
            has_tokens = any(e["type"] == "token" for e in collected_events)

            for event in collected_events:
                yield event

            if not has_tokens and result.content:
                yield {"type": "token", "content": result.content}

            if result.sources and not any(e["type"] == "sources" for e in collected_events):
                yield {
                    "type": "sources",
                    "sources": [
                        {
                            "dish_name": s.dish_name,
                            "category": s.category,
                            "relevance_score": s.relevance_score,
                        }
                        for s in result.sources
                    ],
                }

            final_answer = result.content

        except Exception as e:
            logger.error("Agent execution failed: %s", e)
            yield {"type": "error", "content": f"Agent 执行失败: {e}"}
            return

        # ==== Step 7: 保存回答 ====
        if final_answer:
            await self.repo.add_message(
                session_id=session_id, role="assistant",
                content=final_answer, step_number=99,
            )
            title = user_message[:20] + ("..." if len(user_message) > 20 else "")
            await self.repo.update_session_title(session_id, title)

        yield {"type": "done", "content": final_answer}
```

> **注意：** 上面只列出了 AgentService 的 `stream_agent_chat` 方法变更。CRUD 方法（`create_session`、`list_sessions`、`get_session_detail`、`delete_session`）保持 P0 实现不变。

---

### 2.6 `app/conversation/service.py` — 集成意图识别和查询改写

**修改点 1：** 在 `ConversationService.__init__` 末尾追加 P1 组件初始化：

```python
        # ===== P1 新增：意图识别和查询改写 =====
        from app.conversation.intent import IntentDetector
        from app.conversation.query_rewriter import QueryRewriter
        from app.config import settings

        self.intent_detector = IntentDetector(
            llm_provider=llm_provider,
            redis_client=None,
            cache_ttl=settings.intent_cache_ttl,
        )
        self.query_rewriter = QueryRewriter(
            llm_provider=llm_provider,
            enabled=settings.query_rewriting_enabled,
            max_length=settings.query_rewrite_max_length,
        )
```

**修改点 2：** 在 `stream_chat` 方法中，Step 2（加载历史）之后、Step 3（RAG 检索）之前，插入：

```python
        # ==== Step 2.5: 意图识别 (P1 新增) ====
        try:
            intent = await self.intent_detector.detect(user_message)
            logger.info("Intent detected: %s (confidence=%.2f)", intent.type, intent.confidence)
        except Exception as e:
            logger.warning("Intent detection failed, using default: %s", e)
            from app.conversation.intent import Intent, IntentFilters
            intent = Intent(
                type=settings.intent_fallback,
                confidence=0.3,
                keywords=[user_message],
                filters=IntentFilters(),
            )

        # ==== Step 2.6: 查询改写 (P1 新增) ====
        search_query = user_message
        if settings.query_rewriting_enabled:
            try:
                rewritten = await self.query_rewriter.rewrite(
                    query=user_message,
                    intent_type=intent.type,
                )
                if rewritten != user_message:
                    search_query = rewritten
                    yield {"type": "thought", "content": f"优化查询: {rewritten}"}
            except Exception as e:
                logger.warning("Query rewriting failed: %s", e)

        # ==== Step 3: RAG 检索（使用改写后的查询）====
        yield {"type": "thinking", "content": "正在检索相关菜谱..."}
        try:
            retrieved_docs = await self.rag_service.retrieve(
                query=search_query,  # ← 使用改写后的查询
                user_id=user_id,
            )
        except Exception as e:
            logger.error("RAG retrieval failed: %s", e)
            retrieved_docs = []
```

---

### 2.7 `app/rag/pipeline/retrieval.py` — 追加元数据过滤构建器

在 `RetrievalOptimizationModule` 类末尾追加静态方法：

```python
    @staticmethod
    def build_metadata_filter_expr(
        difficulty: int | None = None,
        category: str | None = None,
        cooking_time_minutes: int | None = None,
        tags: list | None = None,
    ) -> str | None:
        """
        构建 Milvus 标量过滤表达式。

        参数来自 IntentDetector 提取的 filters。

        示例：
          difficulty=2, category="soup"
          → 'difficulty <= 2 and category == "soup"'

        为什么在 Milvus 侧过滤？
          - Milvus 支持标量字段索引过滤，性能远高于 Python 侧后过滤
          - 先过滤再检索保证返回 top_k 个符合条件的结果
          - 如果先取 top_k 再 Python 过滤，可能只剩 2 条有效结果
        """
        parts = []

        if difficulty is not None:
            parts.append(f"difficulty <= {int(difficulty)}")

        if category is not None and category.strip():
            parts.append(f'category == "{category}"')

        if cooking_time_minutes is not None:
            parts.append(f"cooking_time_minutes <= {int(cooking_time_minutes)}")

        if tags:
            for tag in tags:
                parts.append(f'array_contains(tags, "{tag}")')

        return " and ".join(parts) if parts else None
```

---

## 三、新增的空 `__init__.py` 文件

以下文件只需创建空文件（或只有 docstring），用于 Python 包导入：

```
app/conversation/__init__.py        ← 已存在，不需要创建
app/agent/registry/__init__.py      ← 新建，内容: ""（空文件即可）
app/agent/agents/__init__.py        ← 新建，内容: ""（空文件即可）
app/agent/subagents/__init__.py     ← 新建，内容: ""（空文件即可）
app/agent/subagents/builtin/__init__.py ← 新建，内容: ""（空文件即可）
app/rag/rerankers/__init__.py       ← 新建，内容: ""（空文件即可）
```

---

## 四、实施顺序

按依赖关系从底向上，每完成一组可以启动服务验证：

| 顺序 | 文件 | 依赖 | 验证方式 |
|------|------|------|----------|
| 1 | 各目录的 `__init__.py` | 无 | import 不报错 |
| 2 | `prompts.py` | 无 | 纯字符串，import 不报错 |
| 3 | `config.yml` + `config.py` | 无 | 启动 FastAPI，检查 settings 属性 |
| 4 | `agents/base.py` | 无 | import 不报错 |
| 5 | `registry/hub.py` | base.py | 可写简单单元测试验证 register/match |
| 6 | `rerankers/base.py` | 无 | import 不报错 |
| 7 | `rerankers/dashscope_reranker.py` | base.py + DashScope API | 需 API key，可单独测试 |
| 8 | `conversation/intent.py` | prompts.py + LLMProvider | 需 LLM 连接 |
| 9 | `conversation/query_rewriter.py` | prompts.py + LLMProvider | 需 LLM 连接 |
| 10 | `agents/default.py` | base.py + RAGService | 需 RAG 连接 |
| 11 | `subagents/builtin/recipe_master.py` | base.py + RAGService | 需 RAG 连接 |
| 12 | `subagents/builtin/diet_planner.py` | base.py | import 不报错 |
| 13 | `agent/router.py` 修改 | 以上全部 | 启动 FastAPI 查看 Registry 日志 |
| 14 | `agent/service.py` 修改 | router.py | 发一条消息测试完整流程 |
| 15 | `conversation/service.py` 修改 | intent.py + query_rewriter.py | 发消息测试 |
| 16 | `retrieval.py` 追加方法 | 无 | import 不报错 |
