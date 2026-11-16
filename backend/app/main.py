"""FastAPI：浏览器会话、业务查询、确认售后与 SSE 回答。"""

import asyncio
import hashlib
import hmac
import json
import logging
import re
import secrets
import uuid
from contextlib import asynccontextmanager
from weakref import WeakValueDictionary

import anyio
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response, StreamingResponse
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from pydantic import BaseModel, Field
from starlette.datastructures import MutableHeaders

from app import agent, config, knowledge, store

logger = logging.getLogger(__name__)
locks = WeakValueDictionary()
COOKIE = "commerce_visitor"


class BrowserIdentityMiddleware:
    """浏览器随机凭据签名验证；客户范围保存在服务端。"""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not scope.get("path", "").startswith("/api/"):
            return await self.app(scope, receive, send)
        request = Request(scope)
        token, _, signature = request.cookies.get(COOKIE, "").partition(".")
        expected = hmac.new(
            config.SESSION_SECRET.encode(), token.encode(), hashlib.sha256
        ).hexdigest()
        valid = bool(
            re.fullmatch(r"[0-9a-f]{64}", token)
            and re.fullmatch(r"[0-9a-f]{64}", signature)
            and hmac.compare_digest(signature, expected)
        )
        if not valid:
            token = secrets.token_hex(32)
            signature = hmac.new(
                config.SESSION_SECRET.encode(), token.encode(), hashlib.sha256
            ).hexdigest()
        scope.setdefault("state", {})["visitor_id"] = hashlib.sha256(
            token.encode()
        ).hexdigest()

        async def send_cookie(message):
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers["Cache-Control"] = "no-store"
                if not valid:
                    cookie = Response()
                    cookie.set_cookie(
                        COOKIE,
                        token + "." + signature,
                        max_age=30 * 86400,
                        path="/api",
                        httponly=True,
                        samesite="lax",
                        secure=request.url.scheme == "https",
                    )
                    headers.append("set-cookie", cookie.headers["set-cookie"])
            await send(message)

        await self.app(scope, receive, send_cookie)


@asynccontextmanager
async def lifespan(app):
    if len(config.SESSION_SECRET) < 32:
        raise RuntimeError(
            "请先运行 python3 scripts/setup_env.py 生成独立 .env 和 SESSION_SECRET"
        )
    await asyncio.to_thread(store.initialize)
    app.state.knowledge = await asyncio.to_thread(knowledge.initialize)
    app.state.internal_token = secrets.token_hex(32)
    try:
        async with AsyncSqliteSaver.from_conn_string(
            str(config.CHECKPOINT_DB)
        ) as saver:
            await saver.setup()
            app.state.checkpointer = saver
            yield
    finally:
        await asyncio.to_thread(knowledge.close)


app = FastAPI(title="拾光商店客服 Agent", version="0.1.0", lifespan=lifespan)
app.add_middleware(BrowserIdentityMiddleware)


@app.exception_handler(RequestValidationError)
async def validation_error(request, exc):
    return JSONResponse(
        status_code=422, content={"detail": "请求内容不正确，请检查输入长度和字段格式"}
    )


@app.exception_handler(PermissionError)
async def permission_error(request, exc):
    return JSONResponse(status_code=404, content={"detail": str(exc)})


@app.exception_handler(ValueError)
async def business_error(request, exc):
    return JSONResponse(status_code=400, content={"detail": str(exc)})


async def identity(request: Request):
    return await asyncio.to_thread(store.visitor, request.state.visitor_id)


async def same_origin(request: Request):
    origin = request.headers.get("origin")
    if request.headers.get("sec-fetch-site") == "cross-site" or (
        origin and origin != f"{request.url.scheme}://{request.url.netloc}"
    ):
        raise HTTPException(403, "请求来源不正确，请从本站页面操作")
    if request.headers.get("x-client-request") != "1":
        raise HTTPException(403, "请从客服页面提交操作")


@app.get("/api/health")
async def health():
    return {
        "ok": True,
        "demo_mode": config.DEMO_MODE,
        "model": "规则演示" if config.DEMO_MODE else config.CHAT_MODEL,
        "model_configured": config.DEMO_MODE
        or bool(config.CHAT_API_KEY and config.CHAT_BASE_URL),
        "knowledge": app.state.knowledge,
    }


@app.get("/api/session")
async def session(current=Depends(identity)):
    return {
        "customer_id": current["customer_id"],
        "customer_name": "林同学" if current["customer_id"] == "demo-a" else "陈同学",
        "cache_scope": current["id"],
        "demo_notice": "演示身份可切换，仅使用虚构订单，不代表真实用户认证。",
    }


class CustomerRequest(BaseModel):
    customer_id: str


@app.post("/api/session/customer", dependencies=[Depends(same_origin)])
async def switch_customer(body: CustomerRequest, current=Depends(identity)):
    await asyncio.to_thread(store.switch_customer, current["id"], body.customer_id)
    return {"ok": True}


@app.get("/api/products")
async def products():
    return {"products": await asyncio.to_thread(store.products)}


@app.get("/api/orders")
async def orders(current=Depends(identity)):
    return {"orders": await asyncio.to_thread(store.orders, current["customer_id"])}


@app.get("/api/actions")
async def actions(current=Depends(identity)):
    return {
        "actions": await asyncio.to_thread(
            store.actions, current["id"], current["customer_id"]
        )
    }


class Confirmation(BaseModel):
    confirm: bool


@app.post("/api/actions/{action_id}/confirm", dependencies=[Depends(same_origin)])
async def confirm(action_id: str, body: Confirmation, current=Depends(identity)):
    result = await asyncio.to_thread(
        store.confirm_action,
        current["id"],
        current["customer_id"],
        action_id,
        body.confirm,
    )
    return {
        "action": result,
        "notice": "已登记售后工单，待人工审核，未发生退款。"
        if result["status"] == "submitted"
        else "草稿已取消。",
    }


@app.get("/api/conversations")
async def conversations(current=Depends(identity)):
    return {
        "conversations": await asyncio.to_thread(
            store.conversation_list, current["id"], current["customer_id"]
        )
    }


@app.get("/api/conversations/{conversation_id}")
async def conversation(conversation_id: str, current=Depends(identity)):
    result = await asyncio.to_thread(
        store.conversation, current["id"], current["customer_id"], conversation_id
    )
    if result is None:
        raise HTTPException(404, "找不到这段客服会话")
    return {"conversation": result}


class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=2000)


@app.post("/internal/search")
async def internal_search(body: SearchRequest, request: Request):
    if not hmac.compare_digest(
        request.headers.get("x-internal-token", "").encode(),
        app.state.internal_token.encode(),
    ):
        raise HTTPException(403, "内部工具认证失败")
    return {"sources": await asyncio.to_thread(knowledge.search, body.query)}


class ChatRequest(BaseModel):
    question: str = Field(min_length=1, max_length=3000)
    conversation_id: uuid.UUID = Field(default_factory=uuid.uuid4)


def sse(event, data):
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


async def produce(body, current, queue, lock):
    answer = ""
    sources, trace = [], []
    saved = False
    final_save = None
    conversation_id = str(body.conversation_id)
    try:
        async for item in agent.stream(
            body.question.strip(),
            current["id"],
            current["customer_id"],
            conversation_id,
            app.state.checkpointer,
            app.state.internal_token,
        ):
            event, data = item["event"], item["data"]
            if event == "token":
                answer += data["text"]
            elif event == "reset":
                answer = ""
            elif event == "state":
                sources, trace = data["sources"], data["trace"]
            elif event == "result":
                answer, sources, trace = data["answer"], data["sources"], data["trace"]
                final_save = asyncio.create_task(
                    asyncio.to_thread(
                        store.finish_turn, conversation_id, answer, sources, trace
                    )
                )
                await asyncio.shield(final_save)
                saved = True
                queue.put_nowait(
                    (
                        "done",
                        {
                            "answer": answer,
                            "sources": sources,
                            "trace": trace,
                            "conversation_id": conversation_id,
                        },
                    )
                )
                continue
            queue.put_nowait((event, data))
        if not saved:
            raise RuntimeError("回答流意外结束")
    except (Exception, asyncio.CancelledError) as exc:
        if final_save is not None:
            try:
                await final_save
                saved = True
            except Exception:
                logger.exception("保存最终回答失败")
        if not saved:
            interrupted = isinstance(exc, asyncio.CancelledError)
            detail = (
                "已停止生成"
                if interrupted
                else "客服暂时未能完成回答，请检查模型配置和网络后重试。"
            )
            if not interrupted:
                logger.warning("客服生成失败：%s", type(exc).__name__)
            answer = (answer + "\n\n" if answer else "") + detail
            try:
                await asyncio.to_thread(
                    store.finish_turn, conversation_id, answer, sources, trace
                )
            except Exception:
                logger.exception("保存中断回答失败")
            queue.put_nowait(
                (
                    "error",
                    {
                        "detail": detail,
                        "answer": answer,
                        "sources": sources,
                        "trace": trace,
                    },
                )
            )
    finally:
        lock.release()
        queue.put_nowait(None)


@app.post("/api/chat/stream", dependencies=[Depends(same_origin)])
async def chat(body: ChatRequest, current=Depends(identity)):
    if not body.question.strip():
        raise HTTPException(422, "请输入客服问题")
    key = f"{current['id']}:{current['customer_id']}:{body.conversation_id}"
    lock = locks.setdefault(key, asyncio.Lock())
    if lock.locked():
        raise HTTPException(409, "这段对话正在生成，请稍后再试")
    await lock.acquire()
    try:
        await asyncio.to_thread(
            store.begin_turn,
            current["id"],
            current["customer_id"],
            str(body.conversation_id),
            body.question.strip(),
        )
    except BaseException:
        lock.release()
        raise

    async def events():
        queue = asyncio.Queue()
        task = asyncio.create_task(produce(body, current, queue, lock))
        try:
            yield sse("start", {"conversation_id": str(body.conversation_id)})
            while True:
                try:
                    item = await asyncio.wait_for(queue.get(), timeout=15)
                except asyncio.TimeoutError:
                    yield ": 心跳\n\n"
                    continue
                if item is None:
                    break
                yield sse(*item)
        finally:
            if not task.done():
                task.cancel()
            with anyio.CancelScope(shield=True):
                try:
                    await asyncio.wait_for(task, timeout=15)
                except (asyncio.TimeoutError, asyncio.CancelledError):
                    logger.warning("等待客服流清理超时")

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )
