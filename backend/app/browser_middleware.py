"""浏览器匿名身份中间件：签名 Cookie 只保存随机凭据，不暴露访客 ID。"""

import hashlib
import hmac
import re
import secrets

from fastapi import Request, Response
from starlette.datastructures import MutableHeaders

from app import config

COOKIE_NAME = "commerce_visitor"
COOKIE_MAX_AGE = 30 * 24 * 60 * 60


class BrowserIdentityMiddleware:
    """为 API 请求验证或签发匿名浏览器身份。"""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not scope.get("path", "").startswith("/api/"):
            return await self.app(scope, receive, send)

        request = Request(scope)
        token, _, signature = request.cookies.get(COOKIE_NAME, "").partition(".")
        valid = self._valid_cookie(token, signature)
        if not valid:
            token = secrets.token_hex(32)
            signature = self._sign(token)

        # 数据库和 Checkpointer 只拿到不可逆的摘要，浏览器持有签名后的随机令牌。
        scope.setdefault("state", {})["visitor_id"] = hashlib.sha256(
            token.encode()
        ).hexdigest()

        async def send_headers(message):
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers["Cache-Control"] = "no-store"
                if not valid:
                    headers.append(
                        "set-cookie",
                        self._cookie_header(request, token, signature),
                    )
            await send(message)

        await self.app(scope, receive, send_headers)

    @staticmethod
    def _sign(token: str) -> str:
        return hmac.new(
            config.SESSION_SECRET.encode(), token.encode(), hashlib.sha256
        ).hexdigest()

    @classmethod
    def _valid_cookie(cls, token: str, signature: str) -> bool:
        # 先限制格式，避免畸形 Cookie 进入常量时间签名比较。
        if not re.fullmatch(r"[0-9a-f]{64}", token):
            return False
        if not re.fullmatch(r"[0-9a-f]{64}", signature):
            return False
        return hmac.compare_digest(signature, cls._sign(token))

    @staticmethod
    def _cookie_header(request: Request, token: str, signature: str) -> str:
        response = Response()
        response.set_cookie(
            COOKIE_NAME,
            f"{token}.{signature}",
            max_age=COOKIE_MAX_AGE,
            path="/api",
            httponly=True,
            samesite="lax",
            secure=request.url.scheme == "https",
        )
        return response.headers["set-cookie"]
