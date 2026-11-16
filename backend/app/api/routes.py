"""不包含流式生成的 HTTP 路由：状态、访客、商品、订单、工单和历史。"""

import asyncio
import hmac

from fastapi import APIRouter, Depends, HTTPException, Request

from app import config, knowledge, store
from app.api.dependencies import current_visitor, require_same_origin
from app.api.schemas import ConfirmationRequest, CustomerRequest, SearchRequest

router = APIRouter()


@router.get("/api/health")
async def health(request: Request):
    """提供前端所需的模型模式和知识库状态，不返回凭据。"""
    return {
        "ok": True,
        "demo_mode": config.DEMO_MODE,
        "model": "规则演示" if config.DEMO_MODE else config.CHAT_MODEL,
        "model_configured": config.DEMO_MODE
        or bool(config.CHAT_API_KEY and config.CHAT_BASE_URL),
        "knowledge": request.app.state.knowledge,
    }


@router.get("/api/session")
async def get_session(visitor: dict = Depends(current_visitor)):
    return {
        "customer_id": visitor["customer_id"],
        "customer_name": "林同学" if visitor["customer_id"] == "demo-a" else "陈同学",
        "cache_scope": visitor["id"],
        "demo_notice": "演示身份可切换，仅使用虚构订单，不代表真实用户认证。",
    }


@router.post("/api/session/customer", dependencies=[Depends(require_same_origin)])
async def switch_customer(
    body: CustomerRequest, visitor: dict = Depends(current_visitor)
):
    await asyncio.to_thread(store.switch_customer, visitor["id"], body.customer_id)
    return {"ok": True}


@router.get("/api/products")
async def list_products():
    return {"products": await asyncio.to_thread(store.products)}


@router.get("/api/orders")
async def list_orders(visitor: dict = Depends(current_visitor)):
    return {"orders": await asyncio.to_thread(store.orders, visitor["customer_id"])}


@router.get("/api/actions")
async def list_actions(visitor: dict = Depends(current_visitor)):
    return {
        "actions": await asyncio.to_thread(
            store.actions, visitor["id"], visitor["customer_id"]
        )
    }


@router.post(
    "/api/actions/{action_id}/confirm",
    dependencies=[Depends(require_same_origin)],
)
async def confirm_action(
    action_id: str,
    body: ConfirmationRequest,
    visitor: dict = Depends(current_visitor),
):
    result = await asyncio.to_thread(
        store.confirm_action,
        visitor["id"],
        visitor["customer_id"],
        action_id,
        body.confirm,
    )
    notice = (
        "已登记售后工单，待人工审核，未发生退款。"
        if result["status"] == "submitted"
        else "草稿已取消。"
    )
    return {"action": result, "notice": notice}


@router.get("/api/conversations")
async def list_conversations(visitor: dict = Depends(current_visitor)):
    conversations = await asyncio.to_thread(
        store.conversation_list, visitor["id"], visitor["customer_id"]
    )
    return {"conversations": conversations}


@router.get("/api/conversations/{conversation_id}")
async def get_conversation(
    conversation_id: str, visitor: dict = Depends(current_visitor)
):
    result = await asyncio.to_thread(
        store.conversation,
        visitor["id"],
        visitor["customer_id"],
        conversation_id,
    )
    if result is None:
        raise HTTPException(404, "找不到这段客服会话")
    return {"conversation": result}


@router.post("/internal/search")
async def internal_search(body: SearchRequest, request: Request):
    """MCP 子进程专用入口；动态令牌仅在后端进程与工具子进程之间共享。"""
    provided = request.headers.get("x-internal-token", "").encode()
    expected = request.app.state.internal_token.encode()
    if not hmac.compare_digest(provided, expected):
        raise HTTPException(403, "内部工具认证失败")
    return {"sources": await asyncio.to_thread(knowledge.search, body.query)}
