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

import asyncio
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
        system_prompt:str = "", # ← P1 新增：允许自定义 systemprompt
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
        self.system_prompt = system_prompt or AGENT_SYSTEM_PROMPT  #没传就用默认

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
        messages = [SystemMessage(content=self.system_prompt)]

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

            # ═════════════════════════════════════════════════════════════
            # 【P0 修复】用流式调用替代非流式 ainvoke_with_tools
            # ═════════════════════════════════════════════════════════════
            #
            # 旧代码问题：
            #   await llm.ainvoke_with_tools(...) 是非流式调用，LLM 必须生成完
            #   整个回复（包括 tool_call 的 JSON），我们才能知道它想干什么。
            #   ReAct 的每一轮循环（最多 8 轮），用户都要等 1-3 秒看一个"静止画面"。
            #
            # 修复方案：
            #   使用 astream_with_tools 流式调用。LLM 生成的每一个 token 都
            #   立即作为 "thought" 事件推送给前端（用户能看到 Agent 的思考过程）。
            #   同时从 stream chunk 中增量提取 tool_call 信息（name、args）。
            #   流式结束后，如果检测到 tool_calls → 执行工具；
            #   如果没有 tool_calls → 说明是最终回答，token 已经全部发给用户了。
            #
            # 用户体验变化：
            #   旧：等 2秒 → 突然看到 tool_call → 等 1秒 → 突然看到结果
            #   新：看到 Agent 逐字思考 → 看到 tool_call → 看到结果
            #   用户始终知道 Agent "在干什么"，不会对着空白页面等待。
            #
            try:
                # 【流式桥接】_astream_and_detect_tools 是普通 async 函数
                # （返回最终结果），但 LLM 流式生成过程中产生的每一个文本
                # token 都需要实时 yield 给前端。这里用 asyncio.Queue 桥接：
                #
                #   LLM stream chunk → on_thought() → thought_queue
                #        ↓
                #   run() 消费 thought_queue → yield thought 事件
                #
                # 为什么不用更简单的方案？
                #   - yield 只能在 async generator 中用，普通函数无法 yield 到外层
                #   - 所以 _astream_and_detect_tools 用回调 on_thought 通知有新 token
                #   - run() 在后台 task 中并发消费，实时 yield 出去
                thought_queue = asyncio.Queue()

                async def on_thought(text: str):
                    """LLM 每产出一个文本 token，就推入队列。"""
                    await thought_queue.put(text)

                async def run_llm_stream():
                    """在后台 task 中执行流式 LLM 调用。"""
                    return await self._astream_and_detect_tools(
                        messages, on_thought=on_thought,
                    )

                llm_task = asyncio.create_task(run_llm_stream())

                # 在 LLM 生成的同时，从队列中取出 thought token 并实时 yield
                # wait_for(timeout=0.05): 短超时检查 task 是否完成，
                # 避免队列空了之后死等。task 完成后还要把队列中残留的
                # token 全部 drain 干净再退出。
                while not llm_task.done() or not thought_queue.empty():
                    try:
                        text = await asyncio.wait_for(
                            thought_queue.get(), timeout=0.05,
                        )
                        yield {"type": "thought", "content": text}
                    except asyncio.TimeoutError:
                        continue  # 队列暂时为空，LLM 还在生成中

                # LLM 流式完成，获取最终结果
                tool_calls, full_response, ai_message = await llm_task
            except Exception as e:
                logger.error("LLM call failed in agent loop: %s", e)
                yield {"type": "error", "content": f"AI 调用失败: {e}"}
                return

            if tool_calls:
                # ── 路径 A: LLM 决定调用工具 ──
                # tool_calls 是从流式 chunk 中提取的，ai_message 是重建的完整
                # AIMessage（用于追加到对话历史，供下一轮 LLM 调用时参考）

                # 先把 AIMessage（含所有 tool_calls）追加到消息历史
                # 重要：必须在循环外只追加一次。OpenAI API 的格式是：
                #   assistant(tool_calls=[tc1,tc2]) → tool(tc1) → tool(tc2)
                # 如果放在循环内，同一个 AIMessage 会被追加多次。
                messages.append(ai_message)

                for tc in tool_calls:
                    tool_name = tc["name"]
                    tool_args = tc["args"]

                    # 告知前端 Agent 正在调用什么工具
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

                    # 返回工具的观察结果给前端
                    yield {
                        "type": "observation",
                        "tool_name": tool_name,
                        "content": str(observation)[:2000],  # 截断过长的结果
                    }

                    messages.append(ToolMessage(
                        content=str(observation),
                        tool_call_id=tc.get("id", ""),
                    ))
            else:
                # ── 路径 B: LLM 给出了最终回答 ──
                # token 已在 _astream_and_detect_tools 中逐字流式发送给前端，
                # 这里只需要发 done 事件结束本轮对话。
                yield {"type": "done", "content": full_response}
                return  # Agent 完成，退出 ReAct 循环

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
        """从流式 chunk 中提取文本增量。"""
        if hasattr(chunk, "content") and isinstance(chunk.content, str):
            return chunk.content
        if hasattr(chunk, "message") and hasattr(chunk.message, "content"):
            return chunk.message.content or ""
        return ""

    async def _astream_and_detect_tools(self, messages: list, on_thought=None):
        """
        【P0 修复核心方法】流式调用 LLM，并实时检测 tool_call。

        这是替换旧代码中 ainvoke_with_tools 的关键方法。

        旧流程（非流式，阻塞）：
          response = await llm.ainvoke_with_tools(messages, tools)
          tool_calls = extract(response)
          # ↑ 上面两步必须等 LLM 完全生成完，阻塞 1-3 秒

        新流程（流式，边生成边检测）：
          async for chunk in llm.astream_with_tools(messages, tools):
              on_thought(token_text)  ← 回调通知 run() 发送 thought 事件
              accumulate tool_call_chunks  ← 增量收集 tool_call 信息
          tool_calls = build_from_chunks()
          # ↑ LLM 每生成一个 token 用户就看到一个字，同时后台收集 tool_call

        为什么用回调而不是 yield？
          这个方法是普通 async 函数（不是 async generator），因为调用方 run()
          需要 await 它拿到返回值 (tool_calls, full_content, ai_message)。
          Python 不允许同时 yield 和 return ——用回调来传递"中间事件"，
          用 return 来传递"最终结果"。

        LangChain 流式 tool_call 的 chunk 结构：
          每个 AIMessageChunk 可能包含：
            - content: str            ← 文本增量（普通思考内容）
            - tool_call_chunks: list  ← tool_call 增量列表
              每个 tool_call_chunk:
                - index: int    ← 属于第几个 tool_call（从 0 开始）
                - id: str       ← tool_call 的唯一 ID（首个 chunk 出现）
                - name: str     ← 函数名（可能跨多个 chunk 到达）
                - args: str     ← JSON 参数字符串（可能跨多个 chunk 到达）

        参数：
            messages: 当前对话消息列表
            on_thought: async callback(text) — 每收到一个文本 token 就调用

        返回值：
            (tool_calls, full_content, ai_message)
            - tool_calls: [{name, args, id}] — 解析好的工具调用列表，无工具则为 []
            - full_content: str — LLM 生成的完整文本（不含 tool_call JSON）
            - ai_message: AIMessage — 重建的完整消息，用于追加到对话历史
        """
        # 按 index 分组累积 tool_call 增量
        # 因为单个 tool_call 的 name/args 可能跨多个 chunk 到达
        tc_accumulator: dict = {}  # {index: {name, args, id}}

        full_content = ""  # 累积 LLM 生成的文本（思考内容）

        # 流式调用 LLM，绑定了 tools schema
        async for chunk in self.llm_invoker.astream_with_tools(
            messages=messages,
            tools=self._convert_tools_for_langchain(),
        ):
            # ── 提取文本增量 → 通过回调通知 run() 发送 thought 事件 ──
            text = self._extract_chunk_text(chunk)
            if text:
                full_content += text
                if on_thought:
                    await on_thought(text)

            # ── 提取 tool_call 增量 → 累积到 accumulator ──
            # tool_call_chunks 是 LangChain 流式处理 tool calling 的标准方式
            # 每个 chunk 可能包含部分 name 或 args（因为 JSON 可能很长）
            if hasattr(chunk, "tool_call_chunks") and chunk.tool_call_chunks:
                for tc in chunk.tool_call_chunks:
                    idx = tc.get("index", 0)  # 区分同时发生的多个 tool_call
                    if idx not in tc_accumulator:
                        tc_accumulator[idx] = {
                            "name": "",
                            "args": "",
                            "id": tc.get("id", ""),
                        }
                    # name 和 args 可能分片到达，所以用 += 拼接
                    if tc.get("name"):
                        tc_accumulator[idx]["name"] += tc["name"]
                    if tc.get("args"):
                        tc_accumulator[idx]["args"] += tc["args"]

        # ── 流式结束，把所有累积的 tool_call chunk 组装成完整的 tool_call ──
        tool_calls = []
        for idx in sorted(tc_accumulator.keys()):
            tc = tc_accumulator[idx]
            if tc["name"]:  # 有 name 才算有效的 tool_call
                # args 是 JSON 字符串，需要解析为 dict
                try:
                    args = json.loads(tc["args"]) if tc["args"] else {}
                except json.JSONDecodeError:
                    # JSON 解析失败（极少发生，通常是模型 output 被截断）
                    args = {}
                    logger.warning(
                        "Failed to parse tool_call args JSON for '%s': %s",
                        tc["name"], tc["args"][:100],
                    )
                tool_calls.append({
                    "id": tc["id"],
                    "name": tc["name"],
                    "args": args,
                })

        # ── 重建 AIMessage，用于追加到对话历史 ──
        # 流式过程中只有增量 chunk，没有完整的 AIMessage。
        # 但后续的 tool execution 和下一轮 LLM 调用需要把"AI 调用了什么工具"
        # 追加到 messages 列表。所以从累积的数据重建一个 AIMessage。
        #
        # AIMessage 的 tool_calls 格式：[{name, args, id}]
        # LangChain 会自动把它转成 OpenAI API 需要的格式发给 LLM
        # 注意：tool_calls 为空列表时不能传 None，Pydantic 校验要求必须是 list
        ai_message = AIMessage(
            content=full_content,
            tool_calls=[
                {"name": tc["name"], "args": tc["args"], "id": tc["id"]}
                for tc in tool_calls
            ] if tool_calls else [],
        )

        return tool_calls, full_content, ai_message
