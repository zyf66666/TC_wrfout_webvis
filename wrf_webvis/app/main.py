from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from .api.dashboards import router as dashboards_router
from .api.variable_preferences import router as variable_preferences_router
from .api.logs import router as logs_router
from .api.jobs import router as jobs_router
from .api.plots import router as plots_router
from .api.data import router as data_router
from .api.processing_v3 import router as processing_v3_router
from .api.auth import router as auth_router
from .api.users import router as users_router
from .api.figures import router as figures_router
from .core.config import STATIC_ROOT
from .core.access import AccessMiddleware, AccessPolicy

app = FastAPI(title="WRF WebVis")


class FigureCacheMiddleware:
    """看板图片文件名带出图编号、换图就换名，浏览器可以缓存 30 天，不必每次经隧道重新确认（2026-11-06）。"""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not scope["path"].startswith("/static/figures/"):
            await self.app(scope, receive, send)
            return

        async def send_with_cache(message):
            if message["type"] == "http.response.start" and message.get("status") == 200:
                headers = [(k, v) for k, v in message.get("headers", []) if k.lower() != b"cache-control"]
                headers.append((b"cache-control", b"private, max-age=2592000"))
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, send_with_cache)


app.add_middleware(FigureCacheMiddleware)
access_policy = AccessPolicy.from_environment()
app.add_middleware(AccessMiddleware, policy=access_policy)
# Compress responses before they cross the bandwidth-limited SSH tunnel.
app.add_middleware(GZipMiddleware, minimum_size=1000, compresslevel=5)
app.include_router(dashboards_router)
app.include_router(logs_router)
app.include_router(variable_preferences_router)
app.include_router(jobs_router)
app.include_router(plots_router)
app.include_router(data_router)
app.include_router(processing_v3_router)
# 2026-11-06：账号登录（/login、/api/auth/*）与账号管理（/api/users，root 专用）。
app.include_router(auth_router)
app.include_router(users_router)
app.include_router(figures_router)
app.mount("/static", StaticFiles(directory=STATIC_ROOT), name="static")


@app.get("/api/access")
def access_permissions(request: Request):
    # 未登录时也返回 200（authenticated=false），登录页和前端据此判断。
    return JSONResponse(request.state.access, headers={"Cache-Control": "no-store"})


# 功能说明：返回网页应用的首页文件。
@app.get("/")
def index():
    return RedirectResponse(url="/static/index.html")
