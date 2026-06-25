"""
Prompt 输入检测中间件。

两层检测架构：

  Layer 1 — 规则引擎（< 1ms，零成本）
    正则模式匹配 + 长度检查 + 编码异常检测。
    拦截 90%+ 的常见攻击（SQL注入、命令注入、越狱提示词）。

  Layer 2 — LLM 语义检测（~200ms，有 API 成本）
    Fast LLM 判断是否存在语义层面的提示词注入。
    仅在 Layer 1 产生不确定结果时触发（未来实现）。
    当前默认关闭，通过配置 llm_check_enabled 开启。

为什么先做规则再做 LLM？
  —— 类似于 WAF（Web 应用防火墙）的思路：
     规则引擎是第一道快速防线，处理大部分已知攻击模式；
     LLM 是第二道深度防线，处理语义层面的绕过。
     如果先用 LLM，每次请求都要等 200ms + 花 token。
     两层结合：快速拦截已知，精准识别未知。
"""

from typing import Optional

from app.config import settings
from app.security.rules import DENY_PATTERN_GROUPS


class PromptGuard:
    """
    用户输入安全检测器。
    """

    # 异常编码检测的阈值：如果非 ASCII 字符占比超过这个值，
    # 且字符串很短，说明可能是编码混淆攻击
    HIGH_NON_ASCII_RATIO = 0.6
    MIN_LENGTH_FOR_ENCODING_CHECK = 20

    def __init__(self):
        # 把配置中的模式名称转换成实际的规则列表
        # 例如 deny_patterns: ["sql_injection", "jailbreak"]
        #   → self._patterns = SQL_INJECTION_PATTERNS + JAILBREAK_PATTERNS
        self._patterns: list = []
        self._load_patterns()

    def _load_patterns(self):
        """从配置加载需要启用的规则模式。只在初始化时调用一次。"""
        for group_name in settings.prompt_guard.deny_patterns:
            patterns = DENY_PATTERN_GROUPS.get(group_name, [])
            if patterns:
                self._patterns.extend(patterns)

    def check(self, content: str) -> tuple[bool, Optional[str]]:
        """
        检测用户输入是否安全。

        Returns:
            (is_safe, block_reason)
            - is_safe=True: 放行
            - is_safe=False: 拦截，block_reason 说明拦截原因

        为什么返回 tuple 而不是抛异常？
          —— 调用方（GuardManager）需要根据拦截原因来决定是返回 400
             还是只记录日志不拦截。例如：编码异常可能只打警告，
             而 SQL 注入必须拦截。返回 tuple 给调用方灵活处理的空间。
        """
        if not settings.prompt_guard.enabled:
            return (True, None)

        # ---- 检查 1: 消息长度 ----
        if len(content) > settings.prompt_guard.max_message_length:
            return (False, f"message_too_long: max={settings.prompt_guard.max_message_length}")

        # ---- 检查 2: 空消息 ----
        if not content or not content.strip():
            return (False, "empty_message")

        # ---- 检查 3: 正则规则匹配 ----
        for pattern in self._patterns:
            if pattern.search(content):
                return (False, f"pattern_match: {pattern.pattern}")

        # ---- 检查 4: 编码异常检测 ----
        # 为什么需要这个？
        #   攻击者可能用 Unicode 同形字符（homoglyph）绕过关键词过滤。
        #   例如："ｓｙｓｔｅｍ" 使用全角字母，肉眼看着是 "system"，
        #   但正则匹配的是 ASCII 字符，会被绕过。
        #   检测方法：字符串中非 ASCII 字符占比超过 60%，
        #   且字符串长度较短（说明是刻意构造的，不是正常多语言输入）。
        if len(content) >= self.MIN_LENGTH_FOR_ENCODING_CHECK:
            non_ascii = sum(1 for c in content if ord(c) > 127)
            ratio = non_ascii / len(content)
            if ratio > self.HIGH_NON_ASCII_RATIO:
                return (False, f"suspicious_encoding: non_ascii_ratio={ratio:.2f}")

        # 全部检查通过
        return (True, None)


# ---- 单例 ----
# 为什么是单例？
#   —— PromptGuard 是无状态的对象，所有请求共享同一个实例即可。
#   —— 初始化时加载规则，之后不做变更。
#   —— 避免每次请求都 new 一个对象（虽然开销很小，但没必要）。
prompt_guard = PromptGuard()
