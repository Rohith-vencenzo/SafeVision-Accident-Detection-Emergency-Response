from fastapi.responses import JSONResponse

from .config import get_settings


class UploadTooLarge(Exception):
    pass


class UploadLimitMiddleware:
    """Count bytes before multipart parsing, including chunked HTTP uploads."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["path"] != "/api/v1/videos/analyze":
            return await self.app(scope, receive, send)
        maximum = get_settings().max_upload_bytes + 64 * 1024
        headers = dict(scope.get("headers", []))
        length = headers.get(b"content-length", b"")
        if length.isdigit() and int(length) > maximum:
            return await JSONResponse(status_code=413, content={"detail": "video exceeds configured size limit"})(scope, receive, send)
        count = 0
        exceeded = False

        async def counted_receive():
            nonlocal count, exceeded
            message = await receive()
            count += len(message.get("body", b""))
            if count > maximum:
                exceeded = True
                raise UploadTooLarge()
            return message

        async def limited_send(message):
            if not exceeded:
                await send(message)

        try:
            await self.app(scope, counted_receive, limited_send)
        except UploadTooLarge:
            exceeded = True
        if exceeded:
            await JSONResponse(status_code=413, content={"detail": "video exceeds configured size limit"})(scope, receive, send)
