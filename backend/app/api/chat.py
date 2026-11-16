"""聊天流 API：校验会话并将 Agent 事件逐帧写回浏览器。"""

import asyncio
import logging
from weakref import WeakValueDictionary

import anyio
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse

from app import store
from app.api.dependencies import current_visitor, require_same_origin
from app.api.schemas import ChatRequest
from app.services.chat_stream import encode_sse, produce_chat

router = APIRouter()
logger = logging.getLogger(__name__)
conversation_locks: WeakValueDictionary[str, asyncio.Lock] = WeakValueDictionary()


@router.post("/api/chat/stream", dependencies=[Depends(require_same_origin)])
async def stream_chat(
    body: ChatRequest,
    request: Request,
    visitor: dict = Depends(current_visitor),
):
    question = body.question.strip()
    if not question:
        raise HTTPException(422, "请输入客服问题")

    conversation_id = str(body.conversation_id)
    lock_key = f"{visitor['id']}:{visitor['customer_id']}:{conversation_id}"
    conversation_lock = conversation_locks.setdefault(lock_key, asyncio.Lock())
    if conversation_lock.locked():
        raise HTTPException(409, "这段对话正在生成，请稍后再试")

    await conversation_lock.acquire()
    try:
        await asyncio.to_thread(
            store.begin_turn,
            visitor["id"],
            visitor["customer_id"],
            conversation_id,
            question,
        )
    except BaseException:
        conversation_lock.release()
        raise

    async def event_stream():
        queue = asyncio.Queue()
        producer = asyncio.create_task(
            produce_chat(
                question=question,
                conversation_id=conversation_id,
                visitor=visitor,
                checkpointer=request.app.state.checkpointer,
                internal_token=request.app.state.internal_token,
                queue=queue,
                conversation_lock=conversation_lock,
            )
        )
        try:
            yield encode_sse("start", {"conversation_id": conversation_id})
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=15)
                except asyncio.TimeoutError:
                    yield ": 心跳\n\n"
                    continue
                if event is None:
                    break
                yield encode_sse(*event)
        finally:
            if not producer.done():
                producer.cancel()
            # 客户端断开时仍等待 producer 保存中断消息并释放会话锁。
            with anyio.CancelScope(shield=True):
                try:
                    await asyncio.wait_for(producer, timeout=15)
                except (asyncio.TimeoutError, asyncio.CancelledError):
                    logger.warning("等待客服流清理超时")

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )
