"""集成测试专用模型：模拟 Tool Calling，不作为应用运行模式发布。"""

import re
import uuid

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult

from app.tool_results import parse_tool_result

def _tool_result(message: ToolMessage) -> dict:
    return parse_tool_result(message)


class FakeCustomerModel(BaseChatModel):
    """根据集成测试问题生成工具调用和简短最终回答。"""

    @property
    def _llm_type(self) -> str:
        return "integration-test-fake"

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        user_index = next(
            index
            for index in range(len(messages) - 1, -1, -1)
            if isinstance(messages[index], HumanMessage)
        )
        question = str(messages[user_index].content)
        tool_messages = [
            message
            for message in messages[user_index + 1 :]
            if isinstance(message, ToolMessage)
        ]
        completed = {message.name for message in tool_messages}
        order_id = next(iter(re.findall(r"SG\d{8}", question.upper())), "")
        after_sales = any(word in question for word in ("退货", "退款", "售后"))
        apply_action = after_sales and "申请" in question
        if any(word in question for word in ("不申请", "只解释", "只咨询")):
            apply_action = False

        order_intent = bool(order_id) or any(
            word in question for word in ("订单", "物流", "快递")
        )
        recommendation_intent = any(
            word in question for word in ("推荐", "预算", "适合", "用途", "怎么选")
        )
        product_intent = any(
            word in question
            for word in ("商品", "耳机", "阅读灯", "保温杯", "价格", "库存", "多少钱")
        )

        tool_call = self._next_tool(
            question,
            completed,
            order_id,
            after_sales,
            apply_action,
            order_intent,
            recommendation_intent,
            product_intent,
        )
        if tool_call:
            message = AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": tool_call[0],
                        "args": tool_call[1],
                        "id": uuid.uuid4().hex,
                    }
                ],
            )
        else:
            message = AIMessage(content=self._answer(tool_messages))
        return ChatResult(generations=[ChatGeneration(message=message)])

    @staticmethod
    def _next_tool(
        question,
        completed,
        order_id,
        after_sales,
        apply_action,
        order_intent,
        recommendation_intent,
        product_intent,
    ):
        if apply_action and "lookup_orders" not in completed:
            return "lookup_orders", {"order_id": order_id}
        if after_sales and "search_knowledge" not in completed:
            return "search_knowledge", {"query": "退货规则 运费 " + question}
        if apply_action and "prepare_after_sales" not in completed:
            return "prepare_after_sales", {
                "order_id": order_id,
                "reason": question[:500],
            }
        if order_intent and "lookup_orders" not in completed:
            return "lookup_orders", {"order_id": order_id}
        if recommendation_intent and "recommend_products" not in completed:
            budget = re.search(r"(\d+)\s*元", question)
            return "recommend_products", {
                "query": question,
                "max_price_yuan": int(budget.group(1)) if budget else None,
                "limit": 3,
            }
        if product_intent and "search_products" not in completed:
            return "search_products", {"query": question}
        if "search_knowledge" not in completed:
            return "search_knowledge", {"query": question}
        return None

    @staticmethod
    def _answer(tool_messages):
        sections = []
        for message in tool_messages:
            result = _tool_result(message)
            if message.status == "error" or result.get("error"):
                sections.append("工具暂时不可用，请稍后重试。")
            for product in result.get("products", []):
                sections.append(
                    f"{product['name']} ¥{product['price'] / 100:.2f}，库存 {product['stock']} 件。"
                )
            for product in result.get("recommendations", []):
                sections.append(f"推荐{product['name']}，售价 ¥{product['price'] / 100:.2f}。")
            for order in result.get("orders", []):
                sections.append(
                    f"订单 {order['id']}（{order['product_name']}）目前{order['status']}。"
                )
            if message.name == "lookup_orders" and not result.get("orders"):
                sections.append("未找到属于当前演示身份的订单。")
            if result.get("sources"):
                sections.append("根据店铺知识库资料，具体以商品和订单情况为准。")
            if result.get("action"):
                sections.append("已生成售后草稿，请在页面确认提交。")
            if message.name == "recommend_products" and not result.get("recommendations"):
                sections.append("没有找到符合条件的商品。")
        return "\n".join(sections) or "测试模型未生成可显示的结果。"

    async def _astream(self, messages, stop=None, run_manager=None, **kwargs):
        response = self._generate(messages).generations[0].message
        if response.tool_calls:
            yield ChatGenerationChunk(
                message=AIMessageChunk(content="", tool_calls=response.tool_calls)
            )
            return
        for offset in range(0, len(response.content), 24):
            yield ChatGenerationChunk(
                message=AIMessageChunk(content=response.content[offset : offset + 24])
            )
