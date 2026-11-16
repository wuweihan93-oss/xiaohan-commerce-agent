"""无需模型 Key 的规则演示；真实经过 create_agent → MCP → 业务服务，不伪装成大模型。"""

import json
import re
import uuid

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult


def tool_payload(message):
    for part in (
        message.content if isinstance(message.content, list) else [message.content]
    ):
        value = part.get("text", "") if isinstance(part, dict) else part
        try:
            return json.loads(value)
        except (TypeError, ValueError):
            pass
    return {}


def knowledge_excerpt(chunks, question):
    """规则模式只抽取相关短句；完整知识仍保留在页面可展开的依据中。"""
    terms = [
        word
        for word in (
            "运费",
            "七天",
            "退货",
            "保修",
            "发货",
            "配送",
            "包邮",
            "发票",
            "接电",
            "电池",
        )
        if word in question
    ]
    candidates = []
    for chunk in chunks:
        for line in chunk["text"].splitlines():
            if line.startswith("#"):
                continue
            for sentence in re.split(r"(?<=[。；])", line):
                score = sum(word in sentence for word in terms)
                if sentence.strip() and score:
                    candidates.append((score, sentence.strip(), chunk["source"]))
    candidates.sort(key=lambda item: item[0], reverse=True)
    selected = []
    for _, sentence, source in candidates:
        if not any(sentence == item[0] for item in selected):
            selected.append((sentence, source))
        if len(selected) == 2:
            break
    return "".join(item[0] for item in selected) + (
        f"（{selected[0][1]}）" if selected else ""
    )


class DemoCustomerModel(BaseChatModel):
    @property
    def _llm_type(self):
        return "commerce-rule-demo"

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        start = next(
            index
            for index in range(len(messages) - 1, -1, -1)
            if isinstance(messages[index], HumanMessage)
        )
        question = str(messages[start].content)
        current = [
            item for item in messages[start + 1 :] if isinstance(item, ToolMessage)
        ]
        order_id = next(iter(re.findall(r"SG\d{8}", question.upper())), "")
        after_sales = any(
            word in question for word in ("退货", "退款", "取消订单", "售后申请")
        )
        # 咨询退货规则不等于申请售后，必须出现明确的操作意图。
        apply_action = after_sales and any(
            word in question for word in ("申请", "帮我退", "我要退", "取消订单")
        )
        if any(
            word in question
            for word in ("不申请", "不要申请", "只咨询", "只解释", "先不", "别申请")
        ):
            apply_action = False
        order_intent = order_id or any(
            word in question for word in ("订单", "物流", "快递")
        )
        product_intent = any(
            word in question
            for word in (
                "耳机",
                "台灯",
                "阅读灯",
                "保温杯",
                "商品",
                "价格",
                "库存",
                "推荐",
                "多少钱",
            )
        )
        completed = {item.name for item in current}
        name, args = None, {}
        if apply_action and "lookup_orders" not in completed:
            name, args = "lookup_orders", {"order_id": order_id}
        elif after_sales and "search_knowledge" not in completed:
            name, args = (
                "search_knowledge",
                {"query": "七天无理由退货 退款流程 运费 " + question},
            )
        elif apply_action and order_id and "prepare_after_sales" not in completed:
            name, args = (
                "prepare_after_sales",
                {"order_id": order_id, "reason": question[:500]},
            )
        elif order_intent and "lookup_orders" not in completed:
            name, args = "lookup_orders", {"order_id": order_id}
        elif product_intent and "search_products" not in completed:
            name, args = "search_products", {"query": question}
        elif not order_intent and "search_knowledge" not in completed:
            name, args = "search_knowledge", {"query": question}
        if name:
            answer = AIMessage(
                content="",
                tool_calls=[{"name": name, "args": args, "id": uuid.uuid4().hex}],
            )
        else:
            sections = []
            for item in current:
                payload = tool_payload(item)
                if item.status == "error":
                    sections.append("工具暂时失败，无法确认结果，请稍后重试。")
                if payload.get("error"):
                    sections.append(payload["error"])
                for product in payload.get("products", []):
                    sections.append(
                        f"{product['name']} ¥{product['price'] / 100:.2f}，目前有货，库存 {product['stock']} 件。"
                    )
                if item.name == "lookup_orders":
                    if not payload.get("orders"):
                        sections.append(
                            "未找到属于当前演示身份的订单，请核对订单号或切换演示身份。"
                        )
                    for order in payload.get("orders", []):
                        sections.append(
                            f"订单 {order['id']}（{order['product_name']}）目前{order['status']}，最新物流：{order['logistics'][-1]}。"
                        )
                chunks = payload.get("sources", [])
                if chunks and not product_intent and not apply_action:
                    excerpt = knowledge_excerpt(chunks, question)
                    if excerpt:
                        sections.append(excerpt)
                if payload.get("action"):
                    action = payload["action"]
                    # 售后只总结操作结果，订单细节在右侧卡片查看。
                    sections = []
                    if action["status"] == "submitted":
                        sections.append(
                            f"{action['order_id']} 已有待人工审核的售后工单，尚未退款，不会重复创建。"
                        )
                    else:
                        sections.append(
                            f"已为 {action['order_id']} 生成售后草稿，请在“订单与售后”中确认提交。提交后待人工审核，尚未退款。"
                        )
            if apply_action and not order_id:
                sections.append(
                    "如需申请售后，请提供订单号及原因，例如：为 SG20260001 申请退货，原因是不合适。"
                )
            if not sections:
                sections = [
                    "当前是规则演示模式，支持商品、订单物流、退换货与售后草稿。可用页面快捷问题体验，或配置模型后使用自然语言客服。"
                ]
            answer = AIMessage(content="\n\n".join(sections))
        return ChatResult(generations=[ChatGeneration(message=answer)])

    async def _astream(self, messages, stop=None, run_manager=None, **kwargs):
        message = self._generate(messages).generations[0].message
        if message.tool_calls:
            yield ChatGenerationChunk(
                message=AIMessageChunk(content="", tool_calls=message.tool_calls)
            )
        else:
            for offset in range(0, len(message.content), 16):
                yield ChatGenerationChunk(
                    message=AIMessageChunk(
                        content=message.content[offset : offset + 16]
                    )
                )
