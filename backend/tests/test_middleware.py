"""中断后恢复工具消息顺序，防止第二轮模型收到不完整的 tool_calls。"""

import unittest
from unittest.mock import AsyncMock

from app.middleware import ToolRecoveryMiddleware, repair_tool_messages
from langchain.agents.middleware import ModelRequest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from tests.fake_customer_model import FakeCustomerModel


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


class RecommendationRoutingTests(unittest.IsolatedAsyncioTestCase):
    async def test_recommendation_prompt_forces_recommendation_tool(self):
        request = ModelRequest(
            model=FakeCustomerModel(),
            messages=[HumanMessage("推荐一下台灯")],
            tools=[{"name": "recommend_products"}, {"name": "search_products"}],
        )
        handler = AsyncMock(return_value=AIMessage(content="已查询"))

        await ToolRecoveryMiddleware().awrap_model_call(request, handler)

        self.assertEqual(
            handler.await_args.args[0].tool_choice,
            {"type": "function", "function": {"name": "recommend_products"}},
        )

    async def test_non_recommendation_prompt_keeps_model_tool_choice(self):
        request = ModelRequest(
            model=FakeCustomerModel(),
            messages=[HumanMessage("查一下耳机价格")],
            tools=[{"name": "recommend_products"}, {"name": "search_products"}],
        )
        handler = AsyncMock(return_value=AIMessage(content="已查询"))

        await ToolRecoveryMiddleware().awrap_model_call(request, handler)

        self.assertIsNone(handler.await_args.args[0].tool_choice)

    async def test_recommendation_is_not_forced_after_tool_result(self):
        request = ModelRequest(
            model=FakeCustomerModel(),
            messages=[
                HumanMessage("推荐一下台灯"),
                AIMessage(
                    content="",
                    tool_calls=[
                        {"id": "recommend", "name": "recommend_products", "args": {}}
                    ],
                ),
                ToolMessage(
                    content='{"recommendations": []}',
                    tool_call_id="recommend",
                    name="recommend_products",
                ),
            ],
            tools=[{"name": "recommend_products"}],
        )
        handler = AsyncMock(return_value=AIMessage(content="没有符合条件的商品"))

        await ToolRecoveryMiddleware().awrap_model_call(request, handler)

        self.assertIsNone(handler.await_args.args[0].tool_choice)
