"""各 API 路由共用的身份解析与同源请求校验。"""

import asyncio

from fastapi import HTTPException, Request

from app import store


async def current_visitor(request: Request) -> dict:
    """从签名 Cookie 对应的服务端记录读取当前匿名访客。"""
    return await asyncio.to_thread(store.visitor, request.state.visitor_id)


async def require_same_origin(request: Request) -> None:
    """阻止跨站页面伪造会修改访客或售后状态的请求。"""
    origin = request.headers.get("origin")
    expected_origin = f"{request.url.scheme}://{request.url.netloc}"
    if request.headers.get("sec-fetch-site") == "cross-site" or (
        origin and origin != expected_origin
    ):
        raise HTTPException(403, "请求来源不正确，请从本站页面操作")
    if request.headers.get("x-client-request") != "1":
        raise HTTPException(403, "请从客服页面提交操作")
