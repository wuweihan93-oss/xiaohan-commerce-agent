"""Agent 流事件的持久化、错误处理与 SSE 编码。"""

import asyncio
import json
import logging
from typing import Any

from app import agent, store

logger = logging.getLogger(__name__)


def encode_sse(event: str, data: dict[str, Any]) -> str:
    """将事件编码为 SSE 帧；JSON 转义保证正文中的换行不会截断事件。"""
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


async def produce_chat(
    *,
    question: str,
    conversation_id: str,
    visitor: dict,
    checkpointer: Any,
    internal_token: str,
    queue: asyncio.Queue,
    conversation_lock: asyncio.Lock,
) -> None:
    """运行 Agent，将 token/state/result 转成队列事件，并在取消时保存部分回答。"""
    answer = ""
    sources: list[dict] = []
    trace: list[dict] = []
    answer_saved = False
    save_task = None

    try:
        async for event in agent.stream(
            question,
            visitor["id"],
            visitor["customer_id"],
            conversation_id,
            checkpointer,
            internal_token,
        ):
            event_name = event["event"]
            data = event["data"]
            if event_name == "token":
                answer += data["text"]
            elif event_name == "reset":
                # 模型准备调用工具时，清掉此前暂存的文字，避免工具草稿混入最终答案。
                answer = ""
            elif event_name == "state":
                sources, trace = data["sources"], data["trace"]
            elif event_name == "result":
                answer = data["answer"]
                sources, trace = data["sources"], data["trace"]

                # shield 可避免客户端断开时取消已提交的 SQLite 最终回答写入。
                save_task = asyncio.create_task(
                    asyncio.to_thread(
                        store.finish_turn,
                        conversation_id,
                        answer,
                        sources,
                        trace,
                    )
                )
                await asyncio.shield(save_task)
                answer_saved = True
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

            queue.put_nowait((event_name, data))

        if not answer_saved:
            raise RuntimeError("回答流意外结束")
    except (Exception, asyncio.CancelledError) as exc:
        if save_task is not None:
            try:
                await save_task
                answer_saved = True
            except Exception:
                logger.exception("保存最终回答失败")

        if not answer_saved:
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
        # 锁由生产任务释放，而不是 SSE 读取端释放，避免断线后旧任务仍运行时重复提交。
        conversation_lock.release()
        queue.put_nowait(None)
