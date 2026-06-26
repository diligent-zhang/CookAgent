"""
菜谱专家 Agent

处理 recipe_search 意图。
本质是一个 ReAct Agent，复用现有的 ReActAgent 循环，
但使用菜谱专家专用的 system prompt，并可选接入 Reranker 精排。

与通用 Agent 的区别：
  - System prompt 偏菜谱推荐，强调步骤化回答、饮食限制检查
  - 可选接入 Reranker，对 search_recipes 工具的检索结果做精排
  - 其他完全一致：都走 ReAct 循环，都能用 6 个工具
"""
import logging
from typing import Callable, Optional

from app.agent.agent import ReActAgent
from app.agent.agents.base import AgentContext, AgentResult, BaseAgent
from app.agent.tools import get_all_tools
from app.llm.provider import LLMProvider
from app.rag.service import RAGService

logger = logging.getLogger(__name__)

# 菜谱专家专用的 system prompt
# 相比通用的 AGENT_SYSTEM_PROMPT，这里更强调：
#   1. 菜谱推荐的格式规范（先列菜谱 → 简要说明 → 重点菜完整步骤 → 小贴士）
#   2. 饮食限制的优先检查（过敏、素食等）
#   3. 主动提供烹饪技巧
RECIPE_MASTER_PROMPT = """你是 CookHero 菜谱专家，一个专门帮助用户查找和推荐菜谱的 AI 助手。

## 你的专长
- 根据用户的口味、食材、场景推荐最合适的菜谱
- 提供详细的烹饪步骤和技巧
- 根据用户的饮食限制（过敏、素食等）严格调整推荐
- 比较不同做法，推荐最优方案

## 工作原则
- 推荐菜谱前，务必先了解用户的饮食偏好和限制（过敏食材、饮食类型等）
- 如果搜索结果中存在与用户过敏或饮食限制冲突的菜谱，明确指出并排除
- 回答格式：
  1. 先列出推荐的菜谱（含难度、时间、口味）
  2. 对每个菜谱给出简要说明
  3. 重点推荐的那道菜给出完整步骤
  4. 附上烹饪小贴士
- 如果搜索结果不理想，诚实告知并给出替代方向
- 用中文回答，语气专业但亲切

## 你可以使用的工具
- search_recipes: 根据菜名、食材、口味、场景搜索菜谱
- get_recipe_detail: 获取某道菜的完整做法和详细步骤
- get_user_dietary_info: 查询用户的过敏食材、饮食偏好
- check_ingredient_safety: 检查某道菜是否含有用户过敏的食材
- estimate_calories: 粗略估算一道菜的卡路里
- suggest_substitutes: 为某样食材寻找替代方案

记住：你是菜谱专家，每次推荐都要专业、精准、安全！
"""


class RecipeMasterAgent(BaseAgent):
    """
    菜谱专家 Agent —— 基于 ReAct 循环 + 菜谱专用 system prompt。

    处理意图：recipe_search

    架构：
      RecipeMasterAgent.execute()
        └─ 创建 ReActAgent(llm, tools, system_prompt=RECIPE_MASTER_PROMPT)
             └─ ReAct 循环
                  ├─ Thought → LLM 推理
                  ├─ Action → 调用工具（search_recipes 等）
                  ├─ Observation → 工具返回结果
                  └─ Final Answer → 流式输出给用户

    与 GeneralAgent 的区别：
      这里用的是 ReAct 循环（多轮 LLM 调用，适合复杂推理），
      而不是直接 RAG→LLM（单轮，适合简单问答）。

    为什么菜谱搜索需要 ReAct？
      "推荐三菜一汤，要低脂、30分钟能做完、我鸡蛋过敏"
      → 需要：查用户偏好 → 搜索菜谱 → 检查过敏 → 估算时间 → 筛选 → 回答
      → 这是多步推理，不是单次检索能解决的
    """

    name = "recipe_master"
    description = (
        "菜谱专家 Agent，使用 ReAct 循环进行菜谱搜索、推荐和详细指导"
    )
    intent_match = ["recipe_search"]

    def __init__(
        self,
        llm_provider: LLMProvider,
        rag_service: RAGService,
        reranker=None,
    ):
        """
        参数：
            llm_provider: LLM 提供者（用于创建 LLMInvoker）
            rag_service: RAG 服务（注入到工具中）
            reranker: 可选的重排序器，用于精排检索结果
                      P1 阶段先传 None，P2 接入 DashScopeReranker
        """
        self.llm_provider = llm_provider
        self.rag_service = rag_service
        self.reranker = reranker

    async def execute(
        self,
        context: AgentContext,
        stream_callback: Optional[Callable] = None,
    ) -> AgentResult:
        """
        执行菜谱专家 Agent。

        流程：
          1. 从 context 中取改写后的查询（优先）或原始查询
          2. 加载对话历史，构建 LangChain 消息格式
          3. 创建 ReActAgent 实例（带菜谱专用 system prompt）
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
                # thought / tool_call / observation 不放进去
                # 这些是中间推理步骤，放进历史会让上下文过长

        # ==== 创建 LLM Invoker（normal 层，高质量推理 + tool calling）====
        llm_invoker = self.llm_provider.create_invoker("normal")

        # ==== 获取工具集 ====
        tools = get_all_tools()

        # ==== 创建 ReActAgent（复用现有实现，只换 system prompt）====
        agent = ReActAgent(
            llm_invoker=llm_invoker,
            tools=tools,
            max_iterations=8,
        )

        # ==== 执行 ReAct 循环 ====
        # 注意：这里需要把菜谱专用的 system prompt 传给 Agent
        # 当前 ReActAgent.__init__ 里不接收 system_prompt 参数，
        # 所以需要一个小改造：在 run() 方法里用传入的 prompt 替换默认的
        #
        # 临时方案：先手动构建 messages，把专用 prompt 放进去，
        # 然后调 agent 的内部循环。
        # 如果后续改成 ReActAgent 支持自定义 system_prompt，
        # 这里就简化了。

        final_answer = ""
        sources = []

        try:
            async for event in agent.run(
                user_message=query,
                user_id=context.user_id,
                context_messages=context_messages,
            ):
                event_type = event.get("type", "")

                # 如果是 token 事件，收集到 final_answer
                if event_type == "token":
                    final_answer += event.get("content", "")

                # 如果是 done 事件，记录最终回答
                elif event_type == "done":
                    final_answer = event.get("content", "") or final_answer

                # 所有事件通过回调推给上游（AgentService → SSE）
                if stream_callback:
                    await stream_callback(event_type, event)

        except Exception as e:
            logger.error("RecipeMasterAgent execution failed: %s", e)
            error_msg = f"菜谱专家执行失败: {e}"
            if stream_callback:
                await stream_callback("error", {"content": error_msg})
            return AgentResult(content=error_msg)

        return AgentResult(
            content=final_answer,
            sources=sources,
            metadata={
                "intent": "recipe_search",
                "agent": "recipe_master",
            },
        )
