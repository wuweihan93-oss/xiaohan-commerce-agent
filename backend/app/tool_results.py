"""MCP 工具返回值的 JSON 解析。"""

import json

from langchain_core.messages import ToolMessage


def parse_tool_result(message: ToolMessage) -> dict:
    """把 MCP 文本结果解析为字典，供 SSE 状态整理使用。"""
    content = message.content
    parts = content if isinstance(content, list) else [content]
    for part in parts:
        value = part.get("text", "") if isinstance(part, dict) else part
        try:
            result = json.loads(value)
        except (TypeError, ValueError):
            continue
        if isinstance(result, dict):
            return result
    return {}
