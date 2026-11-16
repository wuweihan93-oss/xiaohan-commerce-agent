"""FastAPI 应用入口：生命周期、全局异常映射和路由装配。"""

import asyncio
import secrets
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from app import config, knowledge, store
from app.api.chat import router as chat_router
from app.api.routes import router as api_router
from app.browser_middleware import BrowserIdentityMiddleware


@asynccontextmanager
async def lifespan(app: FastAPI):
    """启动数据库、Milvus 和 Checkpointer；关闭时释放 Milvus 资源。"""
    if len(config.SESSION_SECRET) < 32:
        raise RuntimeError(
            "请先运行 python3 scripts/setup_env.py 生成独立 .env 和 SESSION_SECRET"
        )

    await asyncio.to_thread(store.initialize)
    app.state.knowledge = await asyncio.to_thread(knowledge.initialize)
    # 仅进程内保存的随机密钥供 MCP 子进程查询私有向量检索接口。
    app.state.internal_token = secrets.token_hex(32)

    try:
        async with AsyncSqliteSaver.from_conn_string(
            str(config.CHECKPOINT_DB)
        ) as checkpointer:
            await checkpointer.setup()
            app.state.checkpointer = checkpointer
            yield
    finally:
        await asyncio.to_thread(knowledge.close)


def create_app() -> FastAPI:
    """创建应用并集中注册中间件、异常处理器与路由。"""
    application = FastAPI(
        title="拾光商店客服 Agent",
        version="0.1.0",
        lifespan=lifespan,
    )
    application.add_middleware(BrowserIdentityMiddleware)
    application.add_exception_handler(RequestValidationError, validation_error)
    application.add_exception_handler(PermissionError, permission_error)
    application.add_exception_handler(ValueError, business_error)
    application.include_router(api_router)
    application.include_router(chat_router)
    return application


async def validation_error(request: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=422,
        content={"detail": "请求内容不正确，请检查输入长度和字段格式"},
    )


async def permission_error(request: Request, exc: PermissionError):
    return JSONResponse(status_code=404, content={"detail": str(exc)})


async def business_error(request: Request, exc: ValueError):
    return JSONResponse(status_code=400, content={"detail": str(exc)})


app = create_app()
