"""
意图分类器
使用 fast LLM 将用户输入分类到 5 种意图之一。
支持 Redis 缓存和自动降级
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
    type: str          # recipe_search | cooking_help | diet_plan | ingredient_ask | general_chat
    confidence: float  # 0.0 ~ 1.0
    keywords: list = field(default_factory=list)
    filters: IntentFilters = field(default_factory=IntentFilters)
    raw_response: str = ""  # LLM 原始返回，调试用

    # 缓存 key 前缀
    INTENT_CACHE_PREFIX = "intent"
    # 默认降级意图（LLM 不可用时使用）
    FALLBACK_INTENT = "recipe_search"


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
        return f"{Intent.INTENT_CACHE_PREFIX}:{digest}"

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

        # 提取文本内容
        if hasattr(response, "content"):
            content = response.content
        else:
            content = str(response)

        logger.info("Intent detection took %.2fs for query='%s...'", elapsed, query[:30])
        return content

    def _parse_result(self, raw: str, query: str) -> Intent:
        """解析 LLM 返回的 JSON，失败时降级。"""
        try:
            # 提取 JSON（LLM 可能在 JSON 前后加了 markdown 代码块标记）
            json_str = raw.strip()
            if json_str.startswith("```"):
                # 去掉 ```json ... ``` 包裹
                lines = json_str.split("\n")
                json_str = "\n".join(lines[1:-1])
                json_str = json_str.strip()

            data = json.loads(json_str)

            intent_type = data.get("intent", Intent.FALLBACK_INTENT)
            # 校验意图类型合法性
            valid_intents = {
                "recipe_search", "cooking_help", "diet_plan",
                "ingredient_ask", "general_chat",
            }
            if intent_type not in valid_intents:
                intent_type = Intent.FALLBACK_INTENT

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
            type=Intent.FALLBACK_INTENT,
            confidence=0.3,
            keywords=[query],
            filters=IntentFilters(),
            raw_response="(fallback)",
        )
