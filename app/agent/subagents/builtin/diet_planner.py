"""
饮食规划 Agent（P2 骨架）

P1 阶段只定义接口和骨架方法，实际功能在 P2 实现。

P2 将实现：
  - 制定一周减脂/增肌食谱
  - 记录每日饮食并自动计算热量
  - 获取营养分析报告和改进建议
  - 接入天气 API 做时令推荐
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
    P1 阶段为骨架，P2 实现完整的饮食计划功能。
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
            "- 制定一周减脂/增肌食谱\n"
            "- 记录每日饮食并自动计算热量\n"
            "- 获取营养分析报告和改进建议\n\n"
            "现在你可以先试试菜谱搜索功能哦~"
        )

        if stream_callback:
            await stream_callback("token", {"content": msg})

        return AgentResult(content=msg, metadata={"mode": "skeleton"})
