"""中断后恢复工具消息顺序，防止第二轮模型收到不完整的 tool_calls。"""

import unittest

from app.middleware import repair_tool_messages
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage


class ToolRecoveryTests(unittest.TestCase):
    def test_missing_result_is_repaired_before_next_human_message(self):
        calls = AIMessage(
            content="",
            tool_calls=[
                {"id": "order", "name": "lookup_orders", "args": {}},
                {"id": "policy", "name": "search_knowledge", "args": {"query": "退货"}},
            ],
        )
        result = ToolMessage(content='{"orders": []}', tool_call_id="order")
        repaired = repair_tool_messages(
            [HumanMessage("查订单"), calls, result, HumanMessage("继续")]
        )
        self.assertIs(repaired[2], result)
        self.assertEqual(repaired[3].tool_call_id, "policy")
        self.assertEqual(repaired[3].status, "error")
        self.assertIsInstance(repaired[4], HumanMessage)
        self.assertEqual(repair_tool_messages(repaired), repaired)

    def test_orphan_and_duplicate_results_are_removed(self):
        calls = AIMessage(
            content="",
            tool_calls=[{"id": "order", "name": "lookup_orders", "args": {}}],
        )
        result = ToolMessage(content="订单结果", tool_call_id="order")
        orphan = ToolMessage(content="游离结果", tool_call_id="unknown")
        self.assertEqual(
            repair_tool_messages([orphan, calls, result, result]), [calls, result]
        )
