"""
ReAct Agent 核心

ReAct = Reasoning（推理）+ Acting（行动）

Agent 工作流程：
  用户: "我鸡蛋过敏，有什么清淡的晚餐推荐？"
    ↓
  ┌── ReAct 循环 ──────────────────────────────────┐
  │                                                   │
  │  Step 1: Thought (思考)                           │
  │    "用户鸡蛋过敏，需要推荐不含鸡蛋的清淡晚餐。      │
  │     我需要先查用户偏好，然后搜索清淡晚餐菜谱。"     │
  │                                                   │
  │  Step 2: Action (行动)                            │
  │    调用 get_user_dietary_info("u123")             │
  │                                                   │
  │  Step 3: Observation (观察)                       │
  │    "用户偏好：清淡口味，鸡蛋过敏"                   │
  │                                                   │
  │  Step 4: Thought (再思考)                          │
  │    "用户鸡蛋过敏且喜欢清淡，搜索清淡晚餐菜谱，     │
  │     并检查是否含鸡蛋"                              │
  │                                                   │
  │  Step 5: Action                                   │
  │    调用 search_recipes("清淡晚餐")                 │
  │                                                   │
  │  Step 6: Observation                              │
  │    "找到：清蒸鲈鱼、蒜蓉西兰花、番茄蛋花汤...      │
  │     注意！番茄蛋花汤含鸡蛋！"                      │
  │                                                   │
  │  Step 7: Thought (最终)                            │
  │    "已找到合适菜谱，排除含鸡蛋的，准备回答"         │
  │                                                   │
  │  Step 8: Final Answer                             │
  │    "推荐清蒸鲈鱼和蒜蓉西兰花，清淡无鸡蛋..."       │
  └───────────────────────────────────────────────────┘

关键设计：
  - max_iterations: 最多循环 8 轮，防止无限循环
  - 每轮生成一个 tool_call 或 final_answer
  - 所有步骤通过 SSE 流式推送给前端
  - 思考过程（thought）也推送给前端，让用户看到 Agent "在想什么"
"""

import json
import logging
from datetime import datetime
from typing import Any, AsyncIterator, Dict, List, Optional

from langchain_core.messages import (
    AIMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)

from app.agent.tools import get_all_tools
from app.llm.provider import LLMInvoker

logger = logging.getLogger(__name__)

# Agent System Prompt
AGENT_SYSTEM_PROMPT = """你是CookHero，一个智能烹饪助手 Agent。

你的能力：
  1. 搜索菜谱：根据菜名、食材、口味、场景等条件查找菜谱
  2. 获取菜谱详情：查看某道菜的完整做法
  3. 查询用户饮食偏好：了解用户的过敏食材、饮食类型等
  4. 食材安全检查：检查某道菜是否含用户过敏食材
  5. 估算热量：粗略估算一道菜的卡路里
  6. 食材替换建议：为某样食材找替代方案

工作原则：
  - 在推荐菜谱之前，务必先了解用户的饮食偏好和限制
  - 如果搜索结果中存在与用户过敏或饮食限制冲突的菜谱，要明确指出并排除
  - 回答要具体、步骤化，提供可操作的烹饪指导
  - 如果多次搜索都找不到合适的结果，诚实告知并给出替代方向
  - 用中文回答，语气友好热情

你可以使用以下工具来帮助用户。每次只调用一个工具，等待结果再决定下一步。
"""


class ReActAgent:
    """
    ReAct Agent 实现。

    用法:
        agent = ReActAgent(llm_invoker, tools, max_iterations=8)
        async for step in agent.run(user_message, user_id, context_messages):
            yield step  # SSE 事件
    """

    def __init__(
        self,
        llm_invoker: LLMInvoker,
        tools: List[Any] = None,
        max_iterations: int = 8,
    ):
        """
        初始化 Agent。

        参数：
            llm_invoker: LLM 调用器（支持 tool calling 的 LLMInvoker 实例）
            tools: 工具函数列表
            max_iterations: ReAct 循环最大轮次
        """
        self.llm_invoker = llm_invoker
        self.tools = tools or get_all_tools()
        self.max_iterations = max_iterations
        # 构建工具索引: tool_name → tool_function
        self._tool_map = {tool.name: tool for tool in self.tools}

    async def run(
        self,
        user_message: str,
        user_id: str,
        context_messages: Optional[List[Any]] = None,
    ) -> AsyncIterator[Dict[str, Any]]:
        """
        执行 Agent 的 ReAct 循环，流式返回每一步。

        参数：
            user_message: 用户当前的消息
            user_id: 用户 ID（传给工具函数）
            context_messages: 之前的对话历史（LangChain Message 格式）

        Yields:
            dict: SSE 事件
              - {"type": "thought", "content": "..."}
              - {"type": "tool_call", "tool_name": "...", "arguments": {...}}
              - {"type": "observation", "content": "...", "tool_name": "..."}
              - {"type": "token", "content": "..."}
              - {"type": "done", "message_id": "..."}
              - {"type": "error", "content": "..."}
        """
        # ===== 构建消息列表 =====
        messages = [SystemMessage(content=AGENT_SYSTEM_PROMPT)]

        # 填入历史消息
        if context_messages:
            messages.extend(context_messages)

        # 当前用户消息
        messages.append(HumanMessage(content=user_message))

        # ===== ReAct 循环 =====
        for iteration in range(self.max_iterations):
            logger.info(
                "Agent iteration %d/%d for user %s",
                iteration + 1, self.max_iterations, user_id,
            )

            try:
                # 调用 LLM，让它决定：调用工具 or 直接回答
                response = await self.llm_invoker.ainvoke_with_tools(
                    messages=messages,
                    tools=self._convert_tools_for_langchain(),
                )
            except Exception as e:
                logger.error("LLM call failed in agent loop: %s", e)
                yield {"type": "error", "content": f"AI 调用失败: {e}"}
                return

            # ===== 检查 LLM 是否有工具调用 =====
            tool_calls = self._extract_tool_calls(response)
            if tool_calls:
                # --- 路径 A: LLM 想调用工具 ---
                for tc in tool_calls:
                    tool_name = tc["name"]
                    tool_args = tc["args"]

                    # 向用户告知：Agent 在调用什么工具
                    yield {
                        "type": "tool_call",
                        "tool_name": tool_name,
                        "arguments": tool_args,
                    }

                    # 执行工具
                    try:
                        tool_func = self._tool_map.get(tool_name)
                        if tool_func is None:
                            observation = f"错误：未知工具 '{tool_name}'"
                        else:
                            # 注入真实的 user_id，防止 LLM 编造
                            if tool_func.args_schema and "user_id" in tool_func.args_schema.model_fields:
                                tool_args["user_id"] = user_id
                            # 工具函数有些是 async 有些不是，统一处理
                            if hasattr(tool_func, "ainvoke"):
                                # LangChain @tool 装饰的函数用 ainvoke
                                observation = await tool_func.ainvoke(tool_args)
                            elif hasattr(tool_func, "__call__"):
                                result = tool_func(**tool_args)
                                if hasattr(result, "__await__"):
                                    observation = await result
                                else:
                                    observation = result
                            else:
                                observation = f"错误：无法调用工具 '{tool_name}'"
                    except Exception as e:
                        observation = f"工具调用出错: {e}"
                        logger.error("Tool '%s' failed: %s", tool_name, e)

                    # 返回观察结果
                    yield {
                        "type": "observation",
                        "tool_name": tool_name,
                        "content": str(observation)[:2000],  # 截断过长结果
                    }

                    # 把工具调用和结果追加到消息列表
                    messages.append(response)  # AI 的 tool_call 消息
                    messages.append(ToolMessage(
                        content=str(observation),
                        tool_call_id=tc.get("id", ""),
                    ))
            else:
                # --- 路径 B: LLM 给出了最终回答 ---
                # 重新流式调用，让用户看到逐 token 输出
                yield {"type": "thought", "content": "正在组织回答..."}

                full_response = ""
                try:
                    async for chunk in self.llm_invoker.astream(messages):
                        token_text = self._extract_chunk_text(chunk)
                        if token_text:
                            full_response += token_text
                            yield {"type": "token", "content": token_text}
                except Exception as e:
                    logger.error("Streaming failed: %s", e)
                    yield {"type": "error", "content": f"生成回答失败: {e}"}
                    return

                yield {"type": "done", "content": full_response}
                return  # Agent 完成

        # 超过最大迭代次数
        yield {
            "type": "error",
            "content": f"Agent 在 {self.max_iterations} 轮思考后仍未给出最终回答，请尝试简化问题。"
        }

    # ===== 私有方法 =====

    def _convert_tools_for_langchain(self) -> List[Dict]:
        """
        把 LangChain @tool 函数转为 OpenAI 兼容的 tool schema。

        LangChain 的 @tool 装饰器会自动生成 args_schema，
        这里提取必要字段构建 OpenAI 格式的 tool 定义。
        """
        schemas = []
        for tool in self.tools:
            schema = {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.args_schema.schema()
                    if hasattr(tool, "args_schema") and tool.args_schema
                    else {"type": "object", "properties": {}},
                },
            }
            schemas.append(schema)
        return schemas

    def _extract_tool_calls(self, response) -> List[Dict]:
        """从 LLM 响应中提取工具调用列表。"""
        tool_calls = []

        # LangChain AIMessage 格式
        if hasattr(response, "tool_calls") and response.tool_calls:
            for tc in response.tool_calls:
                tool_calls.append({
                    "id": tc.get("id", ""),
                    "name": tc.get("name", ""),
                    "args": tc.get("args", {}),
                })

        # 兼容某些模型的 additional_kwargs 格式
        if hasattr(response, "additional_kwargs"):
            ak_tool_calls = response.additional_kwargs.get("tool_calls", [])
            for tc in ak_tool_calls:
                if "function" in tc:
                    tool_calls.append({
                        "id": tc.get("id", ""),
                        "name": tc["function"].get("name", ""),
                        "args": json.loads(tc["function"].get("arguments", "{}")),
                    })

        return tool_calls

    def _extract_chunk_text(self, chunk) -> str:
        """从流式 chunk 中提取文本。"""
        if hasattr(chunk, "content") and isinstance(chunk.content, str):
            return chunk.content
        if hasattr(chunk, "message") and hasattr(chunk.message, "content"):
            return chunk.message.content or ""
        return ""
