"""
Agent 抽象基类

定义所有 Agent 必须遵守的接口契约。这是 P1 架构的核心——之前只有一个
ReActAgent 具体类，现在通过 BaseAgent 抽象基类统一接口，调用方只依赖
抽象不依赖具体实现。

引入此基类后，新增 Agent 类型只需实现 execute() 方法并注册即可。

设计决策：
  - 为什么 AgentContext 是 dataclass 而不是 dict？
    dict 访问容易拼错 key，dataclass 有 IDE 自动补全和类型检查
  - 为什么 execute() 接收 stream_callback 而不是返回 AsyncIterator？
    两种 Agent 对流式的需求不同：
      GeneralAgent 内部调 LLM 流式，逐 token 回调
      DietPlannerAgent 直接返回完整文本，不需要流式
    用 callback 统一接口，Agent 自行决定是否逐 token 输出
  - 为什么要抽离数据结构？
    AgentContext 和 AgentResult 是所有 Agent 的输入输出格式。把它们放在
    base.py 里，任何 Agent 实现都引用同一份定义，避免循环导入。
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional


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
    compressed_summary: Optional[str] = None            # 历史对话摘要（长对话时用）
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

    子类必须：
      1. 设置 name / description / intent_match 三个类属性
      2. 实现 execute() 方法

    intent_match 是路由的关键：AgentRegistry 根据这个列表
    把意图类型映射到对应的 Agent。
    """

    name: str = ""                    # 唯一标识符，如 "recipe_master"
    description: str = ""             # 用途说明
    intent_match: List[str] = []      # 匹配哪些意图，如 ["recipe_search"]

    @abstractmethod
    async def execute(
        self,
        context: AgentContext,
        stream_callback: Optional[Callable] = None,
    ) -> AgentResult:
        """
        执行 Agent 逻辑。

        参数：
            context: 包含用户消息、意图、历史等上下文
            stream_callback: 异步回调 async def cb(event_type: str, data: dict)
                            Agent 通过此回调推送 thinking/token/sources 等事件

        返回：
            AgentResult 包含最终回答和相关元数据
        """
        ...

    def can_handle(self, intent_type: str) -> bool:
        """判断此 Agent 是否能处理给定意图。"""
        return intent_type in self.intent_match

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(name={self.name})"
