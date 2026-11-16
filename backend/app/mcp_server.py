"""MCP 子进程只拿到后端注入的访客与客户范围，模型不能自行指定其他客户。"""

import json
import os

from fastmcp import FastMCP

from app import store

mcp = FastMCP("拾光商店客服工具")
VISITOR_ID = os.environ["COMMERCE_VISITOR_ID"]
CUSTOMER_ID = os.environ["COMMERCE_CUSTOMER_ID"]
CONVERSATION_ID = os.environ["COMMERCE_CONVERSATION_ID"]


def output(value):
    return json.dumps(value, ensure_ascii=False)


@mcp.tool
async def search_products(query: str = "") -> str:
    """查询商店商品、实时价格、库存和规格。价格为人民币分，展示时除以 100。"""
    return output({"products": store.products(query)})


@mcp.tool
async def search_knowledge(query: str) -> str:
    """检索退换货、配送、保修和商品选购知识，回答政策时引用返回的文件名。"""
    # 单独进程访问同一个 Milvus Lite 文件可能造成锁冲突，检索通过本进程主 API 转发。
    import httpx

    async with httpx.AsyncClient(timeout=70) as client:
        response = await client.post(
            os.environ["COMMERCE_INTERNAL_URL"] + "/internal/search",
            json={"query": query},
            headers={"X-Internal-Token": os.environ["COMMERCE_INTERNAL_TOKEN"]},
        )
        if response.is_error:
            raise RuntimeError("知识库检索暂时失败，请稍后重试")
        return output(response.json())


@mcp.tool
async def lookup_orders(order_id: str = "") -> str:
    """查询当前演示客户的订单、物流进度；留空列出该客户所有订单，不能访问其他客户。"""
    return output({"orders": store.orders(CUSTOMER_ID, order_id or None)})


@mcp.tool
async def prepare_after_sales(order_id: str, reason: str) -> str:
    """核验订单后创建售后草稿。仅草拟，不提交、不退款；必须让用户点击页面确认按钮。"""
    try:
        return output(
            {
                "action": store.prepare_action(
                    VISITOR_ID, CUSTOMER_ID, CONVERSATION_ID, order_id, reason
                )
            }
        )
    except ValueError as exc:
        return output({"error": str(exc)})


if __name__ == "__main__":
    mcp.run(transport="stdio", show_banner=False)
