"""
Web 搜索工具

提供联网搜索能力，帮助 Agent 获取菜谱库之外的实时信息：
  - 最新食材/菜谱资讯
  - 当季食材、营养知识
  - 健身/减脂等健康话题的最新建议

后端：
  优先使用 Tavily Search API（配置 WEB_SEARCH_API_KEY）；
  未配置时降级到 DuckDuckGo 免费搜索（无需 key，结果质量略低）。
"""
import json
import os
from typing import Optional

import httpx
from langchain_core.tools import tool

# API Key 从环境变量读取（不进入 config.yml 非敏感配置）
_TAVILY_API_KEY = os.environ.get("WEB_SEARCH_API_KEY", "")
_MAX_RESULTS = 6
_TIMEOUT = 10.0

_USER_AGENT = "CookAgent/1.0 (cooking assistant)"


@tool
async def web_search(query: str, max_results: int = _MAX_RESULTS) -> str:
    """
    在互联网上搜索最新信息。当用户询问菜谱库之外的实时/时效性内容时调用，
    如：最新饮食趋势、某些食材的营养新闻、特定菜谱的网上做法、健身饮食建议等。

    参数：
        query: 搜索关键词，中文即可，如 "减脂期吃什么主食"
        max_results: 最多返回几条结果，默认 6，最大 10
    """
    max_results = max(1, min(int(max_results), 10))

    try:
        if _TAVILY_API_KEY:
            results = await _search_tavily(query, max_results)
        else:
            results = await _search_duckduckgo(query, max_results)
    except Exception as e:
        return f"搜索失败: {e}"

    if not results:
        return f"没有找到与「{query}」相关的搜索结果。"

    parts = [f"「{query}」的搜索结果：\n"]
    for i, r in enumerate(results, 1):
        parts.append(f"--- {i}. {r.get('title', '无标题')} ---")
        parts.append(r.get("snippet", ""))
        if r.get("url"):
            parts.append(f"来源: {r['url']}")
        parts.append("")
    return "\n".join(parts)


async def _search_tavily(query: str, max_results: int) -> list:
    """Tavily Search API。"""
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        resp = await client.post(
            "https://api.tavily.com/search",
            json={
                "api_key": _TAVILY_API_KEY,
                "query": query,
                "max_results": max_results,
                "search_depth": "basic",
            },
        )
        resp.raise_for_status()
        data = resp.json()
    return [
        {
            "title": r.get("title", ""),
            "url": r.get("url", ""),
            "snippet": r.get("content", "")[:300],
        }
        for r in data.get("results", [])[:max_results]
    ]


async def _search_duckduckgo(query: str, max_results: int) -> list:
    """DuckDuckGo HTML 搜索降级方案（无需 API key）。"""
    async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=True) as client:
        resp = await client.get(
            "https://html.duckduckgo.com/html/",
            params={"q": query},
            headers={"User-Agent": _USER_AGENT},
        )
        resp.raise_for_status()
        text = resp.text

    # 简单解析 HTML 结果（正则提取标题/摘要/链接）
    import re
    results = []
    # 匹配每个 result：
    # <a ... class="result__a" ... href="...">title</a>
    # <a ... class="result__snippet"...>snippet</a>
    blocks = re.findall(
        r'<a[^>]*class="result__a"[^>]*href="([^"]*)"[^>]*>(.*?)</a>',
        text,
        re.DOTALL,
    )
    snippets = re.findall(
        r'<a[^>]*class="result__snippet"[^>]*>(.*?)</a>', text, re.DOTALL
    )

    def _strip_html(s: str) -> str:
        s = re.sub(r"<[^>]+>", "", s)
        s = s.replace("&amp;", "&").replace("&quot;", '"').replace("&lt;", "<").replace("&gt;", ">")
        s = re.sub(r"\s+", " ", s).strip()
        return s

    for idx, (url, title) in enumerate(blocks[:max_results]):
        snippet = _strip_html(snippets[idx]) if idx < len(snippets) else ""
        results.append({
            "title": _strip_html(title),
            "url": url,
            "snippet": snippet[:300],
        })
    return results