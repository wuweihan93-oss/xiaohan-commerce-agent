"""Agent 主链路：加载 MCP 工具，创建 LangChain Agent 并流式读取消息。"""

import os
import sys
from collections.abc import AsyncIterator
from typing import Any

from langchain.agents import create_agent
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage, ToolMessage
from langchain_mcp_adapters.client import MultiServerMCPClient

from app import config
from app.middleware import ToolRecoveryMiddleware
from app.models import create_chat_model
from app.prompts import SYSTEM_PROMPT
from app.tool_results import parse_tool_result


def mcp_client(
    visitor_id: str,
    customer_id: str,
    conversation_id: str,
    internal_token: str,
) -> MultiServerMCPClient:
    # 明确传递进程身份上下文；模型的工具参数中没有 customer_id 和确认操作。
    env = {
        "PYTHONPATH": str(config.ROOT / "backend"),
        "COMMERCE_DATA_DIR": str(config.DATA_DIR),
        "COMMERCE_VISITOR_ID": visitor_id,
        "COMMERCE_CUSTOMER_ID": customer_id,
        "COMMERCE_CONVERSATION_ID": conversation_id,
        "COMMERCE_INTERNAL_URL": os.getenv(
            "COMMERCE_INTERNAL_URL", "http://127.0.0.1:8020"
        ),
        "COMMERCE_INTERNAL_TOKEN": internal_token,
    }
    return MultiServerMCPClient(
        {
            "commerce": {
                "transport": "stdio",
                "command": sys.executable,
                "args": ["-m", "app.mcp_server"],
                "env": env,
            }
        }
    )


def visible_text(message: AIMessage | AIMessageChunk) -> str:
    content = message.content
    if isinstance(content, str):
        return content
    return "".join(
        item.get("text", "")
        for item in content
        if isinstance(item, dict) and item.get("type") == "text"
    )


def current_state(messages: list[Any]) -> dict[str, Any]:
    start = next(
        (
            i
            for i in range(len(messages) - 1, -1, -1)
            if isinstance(messages[i], HumanMessage)
        ),
        -1,
    )
    sources, trace, recommendations = [], [], []
    for message in messages[start + 1 :]:
        if isinstance(message, AIMessage):
            trace.extend(
                {"tool": call["name"], "status": "running", "label": "Agent 调用工具"}
                for call in message.tool_calls
            )
        if isinstance(message, ToolMessage):
            payload = parse_tool_result(message)
            failed = message.status == "error" or bool(payload.get("error"))
            trace.append(
                {
                    "tool": message.name,
                    "status": "error" if failed else "success",
                    "label": "工具失败" if failed else "MCP 返回结果",
                }
            )
            for source in payload.get("sources", []):
                if source not in sources:
                    sources.append(source)
            if message.name == "recommend_products":
                recommendations = payload.get("recommendations", [])
    return {
        "sources": sources,
        "trace": trace,
        "recommendations": recommendations,
    }


async def stream(
    question: str,
    visitor_id: str,
    customer_id: str,
    conversation_id: str,
    checkpointer: Any,
    internal_token: str,
) -> AsyncIterator[dict[str, Any]]:
    client = mcp_client(visitor_id, customer_id, conversation_id, internal_token)
    # 整轮保持一个 stdio MCP 会话，避免每次调用都重启子进程。
    async with client.session("commerce") as session:
        from langchain_mcp_adapters.tools import load_mcp_tools

        tools = await load_mcp_tools(session)
        # 与教程中的 create_agent 一样：模型、工具、Prompt 和记忆在此组装。
        agent = create_agent(
            model=create_chat_model(),
            tools=tools,
            system_prompt=SYSTEM_PROMPT,
            middleware=[ToolRecoveryMiddleware()],
            checkpointer=checkpointer,
        )
        messages: list[Any] = []
        draft = ""
        message_id = None
        previous = None
        # messages 流负责逐字输出；values 流负责整理本轮完整的工具轨迹与引用。
        agent_config = {
            "configurable": {
                "thread_id": f"{visitor_id}:{customer_id}:{conversation_id}"
            },
            "recursion_limit": 24,
        }
        async for mode, data in agent.astream(
            {"messages": [HumanMessage(content=question)]},
            config=agent_config,
            stream_mode=["messages", "values"],
        ):
            if mode == "messages":
                chunk, metadata = data
                if (
                    not isinstance(chunk, (AIMessage, AIMessageChunk))
                    or metadata.get("langgraph_node") != "model"
                ):
                    continue
                if chunk.id and chunk.id != message_id:
                    message_id = chunk.id
                    if draft:
                        # 部分模型会先输出思考文字再发起工具调用，reset 让前端丢弃草稿。
                        yield {"event": "reset", "data": {}}
                        draft = ""
                text = visible_text(chunk)
                if text:
                    draft += text
                    yield {"event": "token", "data": {"text": text}}
            else:
                messages = data.get("messages", [])
                state = current_state(messages)
                if state != previous:
                    yield {"event": "state", "data": state}
                    previous = state
                if (
                    messages
                    and isinstance(messages[-1], AIMessage)
                    and messages[-1].tool_calls
                    and draft
                ):
                    # 最终答案只保留工具完成后的模型输出，不显示工具调用前的暂存文本。
                    yield {"event": "reset", "data": {}}
                    draft = ""
        final = next(
            (
                item
                for item in reversed(messages)
                if isinstance(item, AIMessage) and not item.tool_calls
            ),
            None,
        )
        if final is None:
            raise RuntimeError("Agent 未生成最终回答")
        yield {
            "event": "result",
            "data": {"answer": visible_text(final), **current_state(messages)},
        }
