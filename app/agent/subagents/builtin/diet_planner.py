"""
饮食规划 Agent（P2 实现）

处理 diet_plan 意图。
本质是一个 ReAct Agent，复用现有的 ReActAgent 循环，
但使用饮食规划师专用的 system prompt，能够调用 plan_weekly_meals
工具生成每日三餐计划，并支持导出为 Markdown 文件。

与 RecipeMasterAgent 的区别：
  - System prompt 偏饮食规划，强调营养搭配、卡路里控制、多天安排
  - 核心工具是 plan_weekly_meals（一步生成完整计划），
    而非 search_recipes（逐步搜索单个菜谱）
  - 支持导出计划文件
"""
import logging
from typing import Callable, Optional

from app.agent.agent import ReActAgent
from app.agent.agents.base import AgentContext, AgentResult, BaseAgent
from app.agent.tools import get_all_tools
from app.llm.provider import LLMProvider
from app.rag.service import RAGService

logger = logging.getLogger(__name__)

# 饮食规划师专用的 system prompt
DIET_PLANNER_PROMPT = """你是 CookHero 饮食规划师，一个专门帮助用户制定科学、健康的饮食计划的 AI 助手。

## 你的专长
- 根据用户的体质目标（减脂/增肌/保持）、卡路里需求、饮食限制制定每日三餐计划
- 确保营养均衡：每餐荤素搭配，全天蛋白质+碳水+蔬菜比例合理
- 严格遵守并提醒用户注意饮食限制（过敏食材、素食、宗教饮食规范等）
- 考虑菜品多样性，相邻两天不安排高度重复的菜品

## 工作流程
1. **了解用户**：首先调用 get_user_dietary_info 查看用户的过敏食材和饮食偏好
2. **确认目标**：与用户确认计划天数、每日卡路里目标、菜系偏好
3. **生成计划**：调用 plan_weekly_meals 工具，传入所有参数，一次性生成完整计划
4. **展示结果**：将计划完整展示给用户，并简要说明设计思路
5. **导出文件**：主动询问用户是否需要导出为文件，若需要则调用 export_meal_plan

## 输出规范
- 展示计划时保持 Markdown 格式清晰
- 主动说明计划的设计逻辑（如"考虑到你的鸡蛋过敏，本周全部避开了含蛋菜品"）
- 最后附上 1-2 条与计划相关的健康小贴士

## 你可以使用的工具
- plan_weekly_meals: 根据参数制定完整的多天饮食计划（早中晚三餐）
- export_meal_plan: 将计划导出为 Markdown 文件
- export_meal_plan_ics: 将计划导出为 ICS 日历文件（可导入手机/电脑日历，推荐）
- export_meal_plan_html: 将计划导出为精美 HTML 网页（彩色日历式表格，适合打印）
- diet_plan: 持久化保存饮食计划，可 add_meal/get_by_week/mark_eaten（生成后主动保存到用户周计划）
- diet_log: 记录用户每天吃了什么，log_from_text 用 AI 自动估算热量
- diet_analysis: 日/周营养汇总、计划 vs 实际偏差分析（基于历史记录）
- get_user_dietary_info: 查询用户的过敏食材、饮食偏好
- search_recipes: 搜索特定菜谱
- get_recipe_detail: 获取某道菜的完整做法
- check_ingredient_safety: 检查菜谱是否含有用户过敏的食材
- estimate_calories: 估算菜品的卡路里
- suggest_substitutes: 为特定食材寻找替代品
- get_current_datetime: 获取当前日期时间
- calculator: 数学计算

记住：你是专业的饮食规划师，你的计划要科学、可执行、个性化！
"""


class DietPlannerAgent(BaseAgent):
    """
    饮食规划 Agent —— 基于 ReAct 循环 + 饮食规划专用 system prompt。

    处理意图：diet_plan

    架构：
      DietPlannerAgent.execute()
        └─ 创建 ReActAgent(llm, tools, system_prompt=DIET_PLANNER_PROMPT)
             └─ ReAct 循环
                  ├─ Thought → "先查用户偏好"
                  ├─ Action → get_user_dietary_info()
                  ├─ Observation → "鸡蛋过敏，偏好家常菜"
                  ├─ Thought → "用这些参数调用 plan_weekly_meals"
                  ├─ Action → plan_weekly_meals(days=7, ...)
                  ├─ Observation → (完整饮食计划)
                  ├─ Thought → "计划已生成，询问是否导出"
                  ├─ Action → export_meal_plan(content)
                  └─ Final Answer → 计划 + 导出确认
    """

    name = "diet_planner"
    description = "饮食规划 Agent，负责制定每日三餐饮食计划并支持导出为文件"
    intent_match = ["diet_plan"]

    def __init__(self, llm_provider: LLMProvider, rag_service: RAGService):
        self.llm_provider = llm_provider
        self.rag_service = rag_service

    async def execute(
        self,
        context: AgentContext,
        stream_callback: Optional[Callable] = None,
    ) -> AgentResult:
        """
        执行饮食规划 Agent。

        流程：
          1. 从 context 中取改写后的查询（优先）或原始查询
          2. 加载对话历史，构建 LangChain 消息格式
          3. 创建 ReActAgent 实例（带饮食规划专用 system prompt）
          4. 启动 ReAct 循环，所有事件通过 stream_callback 推送
          5. 收集最终回答，返回 AgentResult
        """
        from langchain_core.messages import AIMessage, HumanMessage

        query = context.rewritten_query or context.original_query

        # ==== 构建历史消息（LangChain 格式）====
        context_messages = []
        for msg in context.recent_messages:
            if hasattr(msg, "role") and hasattr(msg, "content"):
                if msg.role == "user":
                    context_messages.append(HumanMessage(content=msg.content))
                elif msg.role == "assistant":
                    context_messages.append(AIMessage(content=msg.content))

        # ==== 创建 LLM Invoker（normal 层，高质量推理 + tool calling）====
        llm_invoker = self.llm_provider.create_invoker("normal")

        # ==== 获取工具集（包含新增的 plan_weekly_meals 和 export_meal_plan）====
        tools = get_all_tools()

        # ==== 创建 ReActAgent，注入饮食规划师专用 system prompt ====
        agent = ReActAgent(
            llm_invoker=llm_invoker,
            tools=tools,
            max_iterations=8,
            system_prompt=DIET_PLANNER_PROMPT,
        )

        final_answer = ""

        try:
            async for event in agent.run(
                user_message=query,
                user_id=context.user_id,
                context_messages=context_messages,
            ):
                event_type = event.get("type", "")

                if event_type == "token":
                    final_answer += event.get("content", "")
                elif event_type == "done":
                    final_answer = event.get("content", "") or final_answer

                if stream_callback:
                    await stream_callback(event_type, event)

        except Exception as e:
            logger.error("DietPlannerAgent execution failed: %s", e)
            error_msg = f"饮食规划师执行失败: {e}"
            if stream_callback:
                await stream_callback("error", {"content": error_msg})
            return AgentResult(content=error_msg)

        return AgentResult(
            content=final_answer,
            metadata={
                "intent": "diet_plan",
                "agent": "diet_planner",
            },
        )
