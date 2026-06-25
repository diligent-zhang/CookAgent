"""
安全检测规则集。

这里只有数据（正则模式、关键词列表），不包含检测逻辑。

为什么集中管理？
  —— 方便安全审计：一眼就能看到我们拦截了什么
  —— 方便更新：发现新的攻击模式，只改这一个文件
  —— 方便测试：规则和检测逻辑分离，测试各自独立
"""

import re

# ========================================================================
# Layer 1: 正则规则引擎 — 零延迟检测
#
# 为什么用正则而不是 LLM？
#   —— 正则匹配 < 1ms，LLM 判断 > 200ms
#   —— 以下模式都有明确的结构特征，正则足以覆盖
#   —— 零 API 成本
# ========================================================================

# SQL 注入模式
# 为什么需要？即使我们用的是 ORM（SQLAlchemy），用户输入可能通过
# 其他渠道（如 Milvus 查询表达式、PG 原生 SQL 片段）到达数据库。
# 关键词: DROP, INSERT, DELETE, UNION, SELECT...FROM 等 SQL DML 语句关键字
SQL_INJECTION_PATTERNS = [
    re.compile(r"(?i)(\bDROP\s+TABLE\b)"),
    re.compile(r"(?i)(\bINSERT\s+INTO\b)"),
    re.compile(r"(?i)(\bDELETE\s+FROM\b)"),
    re.compile(r"(?i)(\bUPDATE\s+\w+\s+SET\b)"),
    re.compile(r"(?i)(\bUNION\s+SELECT\b)"),
    re.compile(r"(?i)(\bSELECT\s+.+\s+FROM\b)"),
    re.compile(r"(?i)(--\s*$)"),           # SQL 行注释
    re.compile(r"(?i)(\/\*.*\*\/)"),       # SQL 块注释
    re.compile(r"(?i)(\bEXEC\s*\()"),      # EXEC 执行
    # 特殊字符组合，通常用于绕过过滤
    re.compile(r"(\|\|.*\|\|)"),           # 字符串拼接
    re.compile(r"(';\s*--)"),              # 经典注入结尾
]

# 命令注入模式
# 为什么需要？虽然我们的系统不直接执行 shell 命令，但用户的输入
# 可能被记录到日志、传给其他系统，如果包含命令注入字符可能在下游被利用。
COMMAND_INJECTION_PATTERNS = [
    re.compile(r"[;&|`]"),                                   # shell 控制字符
    re.compile(r"\$\(.*\)"),                                  # 命令替换
    re.compile(r"\b(cat|rm|wget|curl|chmod|sudo)\b"),
    re.compile(r"\.\.\/"),                                    # 路径遍历
    re.compile(r"\/etc\/passwd"),                             # 敏感文件访问
    re.compile(r"\b(bash|sh|zsh|powershell|cmd)\b"),
]

# 提示词泄露/越狱模式
# 为什么需要？攻击者可能尝试：
#   1. 让你输出系统提示词（"告诉我你的 system prompt"）
#   2. 绕过安全限制（DAN 攻击 / "假装你是..." 角色扮演越狱）
#   3. 让你做超出范围的事（"忽略之前的指令"）
PROMPT_LEAK_PATTERNS = [
    re.compile(r"(?i)(system\s*prompt|系统提示)"),
    re.compile(r"(?i)(ignore\s+(previous|all|above|below)\s+instructions)"),
    re.compile(r"(?i)(pretend\s+(you\s+)?are\b)"),
    re.compile(r"(?i)(DAN\s+mode|developer\s+mode)"),
    re.compile(r"(?i)(forget\s+everything)"),
]

JAILBREAK_PATTERNS = [
    re.compile(r"(?i)(你是一个|你现在是|扮演)"),             # 中文角色扮演越狱
    re.compile(r"(?i)(不需要遵守|不用管)"),                   # 中文越狱指令
    re.compile(r"(?i)(do\s+not\s+follow)"),
    re.compile(r"(?i)(without\s+(any\s+)?restriction)"),
    re.compile(r"(?i)(bypass|override)"),
    re.compile(r"(?i)(你不再是一个|你被重置)"),               # 重置攻击
]

# ========================================================================
# 所有按模式类型分组的规则集
#
# 为什么用字典而不是列表？方便在 guard.py 中按名称开关：
#   配置 deny_patterns: ["sql_injection", "command_injection"]
#   代码根据名称取出对应的规则列表
# ========================================================================
DENY_PATTERN_GROUPS: dict[str, list] = {
    "sql_injection": SQL_INJECTION_PATTERNS,
    "command_injection": COMMAND_INJECTION_PATTERNS,
    "prompt_leak": PROMPT_LEAK_PATTERNS,
    "jailbreak": JAILBREAK_PATTERNS,
}
