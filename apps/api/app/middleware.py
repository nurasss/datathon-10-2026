"""Authenticate before parsing uploads; bound multipart input even without Content-Length."""

from fastapi import HTTPException
from fastapi.responses import JSONResponse

from .security import check_api_key


class UploadGuard:
    def __init__(self, app, settings):
        self.app = app
        self.settings = settings

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        path = scope["path"]
        headers = dict(scope["headers"])
        if path == "/api/v1/analyze" or path.startswith("/api/v1/jobs/"):
            key = headers.get(b"x-api-key", b"").decode("latin1")
            if check_api_key(key, self.settings.api_keys) is None:
                return await JSONResponse(status_code=401, content={
                    "detail": "Неверный или отсутствующий X-API-Key",
                })(scope, receive, send)
        if scope["method"] != "POST" or path not in ("/api/v1/analyze", "/ui/analyze"):
            return await self.app(scope, receive, send)

        # File limit is checked separately, precisely. Allow bounded multipart overhead.
        limit = self.settings.max_upload_bytes + 64 * 1024
        try:
            length = int(headers.get(b"content-length", b"0"))
        except ValueError:
            length = 0
        if length > limit:
            return await JSONResponse(status_code=413, content={
                "detail": "Превышен лимит загрузки (100 МБ + multipart overhead)",
            })(scope, receive, send)
        consumed = 0

        async def limited_receive():
            nonlocal consumed
            message = await receive()
            consumed += len(message.get("body", b""))
            if consumed > limit:
                raise HTTPException(status_code=413, detail="Превышен лимит загрузки")
            return message

        await self.app(scope, limited_receive, send)
