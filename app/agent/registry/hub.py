"""
Agent 注册中心

管理所有 Agent 的注册、查找和意图路由。
使用 Registry Pattern（注册模式），新增 Agent 只需注册，无需修改调度代码。

作用：管理所有 Agent，提供意图 → Agent 的路由功能。用注册模式替代硬编码
——新增 Agent 只需 registry.register(xxx)，不需要改调度代码。

设计决策：
  - 为什么是实例注册而不是类注册？
    实例注册可以直接注入依赖（LLMProvider、RAGService），
    类注册还需要额外的工厂方法，多一层间接。
  - 为什么需要 default Agent？
    某些新意图可能没有专门的 Agent 处理，default 保证总有 Agent 兜底。
  - 为什么用单例？
    注册中心需要在应用启动时注册 Agent，在每次请求时查找 Agent。
    单例保证注册和查找操作的是同一个实例。
"""
import logging
from typing import Dict, List, Optional
from app.agent.agents.base import BaseAgent

logger = logging.getLogger(__name__)


class AgentRegistry:
    """
    Agent 注册中心。

    用法示例：
        registry = AgentRegistry()
        registry.register(RecipeMasterAgent(llm, rag))
        registry.register(GeneralAgent(llm, rag))
        registry.set_default("general")

        # 每次请求时：
        agent = registry.match("recipe_search")
        result = await agent.execute(context, callback)
    """

    def __init__(self):
        self._agents: Dict[str, BaseAgent] = {}       # name → Agent 实例
        self._intent_routing: Dict[str, str] = {}     # intent_type → agent_name
        self._default_name: Optional[str] = None

    def register(self, agent: BaseAgent) -> None:
        """注册一个 Agent 实例，自动建立意图路由映射。"""
        if not agent.name:
            raise ValueError("Agent name cannot be empty")

        self._agents[agent.name] = agent

        # 把该 Agent 声明的每个意图都映射到它
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
        """设置默认 Agent（无匹配意图时兜底）。"""
        if agent_name not in self._agents:
            raise ValueError(f"Agent '{agent_name}' not registered")
        self._default_name = agent_name

    def get(self, name: str) -> Optional[BaseAgent]:
        """按名称获取 Agent。"""
        return self._agents.get(name)

    def match(self, intent_type: str) -> BaseAgent:
        """根据意图类型匹配合适的 Agent。

        匹配逻辑：
          1. 查 intent → agent 映射表
          2. 命中 → 返回对应 Agent
          3. 未命中 → 返回 default Agent
          4. 没有 default → 抛异常
        """
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


# ===== 全局单例 =====

_global_registry: Optional[AgentRegistry] = None


def get_agent_registry() -> AgentRegistry:
    """获取全局 AgentRegistry 单例。"""
    global _global_registry
    if _global_registry is None:
        _global_registry = AgentRegistry()
    return _global_registry


def reset_agent_registry() -> None:
    """重置全局注册中心（仅测试用）。"""
    global _global_registry
    _global_registry = AgentRegistry()
