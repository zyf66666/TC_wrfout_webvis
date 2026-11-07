"""谁在访问、能做什么（2026-11-06 改为账号登录）。

- 本机直连（127.0.0.1 / ::1，且没有 nginx 转发头）＝ root，免登录。本机命令行工具走这里。
- 公网经 nginx + SSH 隧道进来的请求带 X-Real-IP / X-Forwarded-For，一律要登录：
  未登录只能打开登录页；root 账号拥有全部权限；普通账号默认什么都不能做，只开放
  它被勾选的看板、页面和（勾了“可批注”时）批注接口。看板编号的检查在 api/dashboards.py。
- 以前按 IP 白名单（WRF_WEBVIS_EDITOR_IPS）自动可写的做法已取消。
"""
from __future__ import annotations

import ipaddress
import os
import re
from dataclasses import dataclass
from http.cookies import SimpleCookie
from urllib.parse import quote, urlsplit

from fastapi import HTTPException, Request
from starlette.datastructures import Headers
from starlette.responses import JSONResponse, PlainTextResponse, RedirectResponse

from ..storage import user_store as user_store_module
from ..storage.user_store import ANONYMOUS, LOCAL_ROOT, Principal


DEFAULT_TRUSTED_PROXY_IPS = "127.0.0.1,::1"
DEFAULT_LOCAL_ROOT_IPS = "127.0.0.1,::1"
SESSION_COOKIE = "wrfwebvis_session"
READ_ONLY_DETAIL = {"code": "READ_ONLY", "message": "当前账号没有修改权限，不能执行此操作"}
LOGIN_DETAIL = {"code": "LOGIN_REQUIRED", "message": "请先登录"}
FORBIDDEN_DETAIL = {"code": "FORBIDDEN", "message": "当前账号没有权限访问这里"}
ORIGIN_DETAIL = {"code": "BAD_ORIGIN", "message": "请求来源与网站不一致，已拒绝"}
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})

# 未登录也能打开的地址：登录页和它自己的样式/脚本、登录接口、权限查询。
PUBLIC_PATHS = frozenset({"/login", "/api/auth/login", "/api/access", "/favicon.ico",
                          "/static/login.css", "/static/login.js"})
# 普通账号勾了“可批注”后可用的写接口（看板编号、批注作者在路由里再查）。
_ANNOTATION_WRITE = re.compile(
    r"^/api/dashboards/[^/]+/images/[^/]+/(note|annotations|annotations/[^/]+)$")
# 勾了“可出图”后可用的接口（看板编号、请求内容、任务归属在路由里再查，见 services/plot_permissions.py）。
_PLOT_WRITE = re.compile(r"^/api/dashboards/[^/]+/images/jobs$|^/api/plots/dry-run$|^/api/jobs/[^/]+/cancel$"
                         r"|^/api/dashboards/[^/]+/images/[^/]+/restore$")
# 出图账号删除自己出的图（只放行 DELETE；改名等仍只给 root）
_IMAGE_DELETE = re.compile(r"^/api/dashboards/[^/]+/images/[^/]+$")
_JOB_PROGRESS = re.compile(r"^/api/jobs/[^/]+/progress$")


def _networks(value: str, name: str) -> tuple:
    try:
        return tuple(ipaddress.ip_network(item.strip(), strict=True) for item in value.split(",") if item.strip())
    except ValueError as exc:
        raise ValueError(f"{name} 包含无效 IP 或 CIDR 网段") from exc


def _address(value: str):
    try:
        address = ipaddress.ip_address(value.strip())
        # IPv4-mapped connections must use the same permissions as native IPv4.
        return getattr(address, "ipv4_mapped", None) or address
    except ValueError:
        return None


def _matches(address, networks: tuple) -> bool:
    return address is not None and any(address in network for network in networks)


def session_token(scope) -> str | None:
    header = Headers(scope=scope).get("cookie")
    if not header:
        return None
    cookie = SimpleCookie()
    try:
        cookie.load(header)
    except Exception:  # noqa: BLE001 - 坏 cookie 当作没登录
        return None
    morsel = cookie.get(SESSION_COOKIE)
    return morsel.value if morsel else None


@dataclass(frozen=True)
class AccessPolicy:
    trusted_proxies: tuple
    local_roots: tuple

    @classmethod
    def from_environment(cls):
        return cls(
            _networks(os.environ.get("WRF_WEBVIS_TRUSTED_PROXY_IPS", DEFAULT_TRUSTED_PROXY_IPS), "WRF_WEBVIS_TRUSTED_PROXY_IPS"),
            _networks(os.environ.get("WRF_WEBVIS_LOCAL_ROOT_IPS", DEFAULT_LOCAL_ROOT_IPS), "WRF_WEBVIS_LOCAL_ROOT_IPS"),
        )

    def client(self, scope) -> tuple:
        """返回 (来源地址, 是否经 nginx 转发)。转发头不合规时地址为 None（按未登录处理）。"""
        client = scope.get("client")
        address = _address(client[0]) if client else None
        headers = Headers(scope=scope)
        real = headers.getlist("x-real-ip")
        forwarded = headers.getlist("x-forwarded-for")
        if _matches(address, self.trusted_proxies) and (real or forwarded):
            # Nginx overwrites both fields with ONE connection source. Never
            # fall back to the tunnel's loopback peer if that source is invalid.
            if len(real) != 1 or len(forwarded) != 1:
                return None, True
            actual, claimed = _address(real[0]), _address(forwarded[0])
            return (actual if actual is not None and actual == claimed else None), True
        return address, False

    def is_local(self, scope) -> bool:
        address, via_proxy = self.client(scope)
        return not via_proxy and _matches(address, self.local_roots)

    def principal(self, scope) -> Principal:
        if self.is_local(scope):
            return LOCAL_ROOT
        found = user_store_module.store.principal_for_token(session_token(scope))
        return found or ANONYMOUS

    def permissions(self, scope) -> dict:
        """不查登录会话的简化判断（只看是不是本机），给单独挂载的路由和旧代码用。"""
        return (LOCAL_ROOT if self.is_local(scope) else ANONYMOUS).as_access()


# ---------------------------------------------------------------------- 路径规则
def _page_for_read(path: str) -> str | None:
    """普通账号读请求：返回需要的页面权限；"" 表示登录即可；None 表示不开放。"""
    if path in ("/", "/static/index.html"):
        return ""
    if path.startswith("/static/"):
        sub = path[len("/static/"):]
        return ""  # 前端脚本、样式和看板图片（/static/figures/）
    if path.startswith("/api/figures/thumb/"):
        return ""  # 看板图片的缩略图，和 /static/figures/ 一样登录即可
    if path == "/api/dashboards" or path.startswith("/api/dashboards/"):
        return None if path.endswith("/deleted-images") else ""
    if path.startswith(("/api/data/", "/api/processing/v3/")) or path == "/api/jobs" or path.startswith("/api/jobs/"):
        return "data"
    return None


def authorize(principal: Principal, method: str, path: str) -> str:
    """返回 "allow" / "login" / "forbidden"。"""
    if path in PUBLIC_PATHS:
        return "allow"
    if principal.is_root:
        return "allow"
    if not principal.is_authenticated:
        return "login"
    if path.startswith("/api/auth/"):
        return "allow"
    if method.upper() in SAFE_METHODS:
        page = _page_for_read(path)
        if page == "" or (page and principal.can_see_page(page)):
            return "allow"
        # 看绘图设置时，绘图界面要读“常用变量”；出图后要轮询自己任务的进度
        if path == "/api/variable-preferences" and principal.can_see_plot_settings:
            return "allow"
        if principal.may_plot and _JOB_PROGRESS.fullmatch(path):
            return "allow"
        return "forbidden"
    if principal.can_annotate and _ANNOTATION_WRITE.fullmatch(path):
        return "allow"
    if principal.may_plot and _PLOT_WRITE.fullmatch(path):
        return "allow"
    if principal.may_plot and method.upper() == "DELETE" and _IMAGE_DELETE.fullmatch(path):
        return "allow"
    return "forbidden"


def _same_origin(scope) -> bool:
    headers = Headers(scope=scope)
    origin = headers.get("origin")
    if origin is None:
        return True  # 命令行工具等不带 Origin 的请求
    host = headers.get("host", "")
    parts = urlsplit(origin)
    return bool(parts.netloc) and parts.netloc.lower() == host.lower()


class AccessMiddleware:
    def __init__(self, app, policy: AccessPolicy):
        self.app = app
        self.policy = policy

    async def __call__(self, scope, receive, send):
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        principal = self.policy.principal(scope)
        state = scope.setdefault("state", {})
        state["principal"] = principal
        state["access"] = principal.as_access()
        if scope["type"] == "websocket":
            if not principal.is_root:
                await send({"type": "websocket.close", "code": 1008})
                return
            await self.app(scope, receive, send)
            return
        method, path = scope["method"].upper(), scope["path"]
        if method not in SAFE_METHODS and not _same_origin(scope):
            await _json(403, ORIGIN_DETAIL)(scope, receive, send)
            return
        decision = authorize(principal, method, path)
        if decision == "allow":
            await self.app(scope, receive, send)
            return
        is_api = path.startswith("/api/") or method not in SAFE_METHODS
        if decision == "login":
            if is_api:
                response = _json(401, LOGIN_DETAIL)
            else:
                query = scope.get("query_string", b"").decode("latin-1")
                target = path + (f"?{query}" if query else "")
                response = RedirectResponse(f"/login?next={quote(target, safe='')}", status_code=303,
                                            headers={"Cache-Control": "no-store"})
        elif is_api:
            response = _json(403, READ_ONLY_DETAIL if method not in SAFE_METHODS else FORBIDDEN_DETAIL)
        else:
            response = PlainTextResponse("当前账号没有权限打开这个页面。", status_code=403,
                                         headers={"Cache-Control": "no-store"})
        await response(scope, receive, send)


def _json(status: int, detail: dict) -> JSONResponse:
    return JSONResponse({"detail": detail}, status_code=status, headers={"Cache-Control": "no-store"})


# ---------------------------------------------------------------------- 路由里用
def current_principal(request: Request) -> Principal:
    principal = getattr(request.state, "principal", None)
    if principal is None:
        # 路由被单独挂载（测试、其他程序）时没有中间件：按来源和会话 cookie 重新判断。
        principal = AccessPolicy.from_environment().principal(request.scope)
    return principal


def require_write(request: Request) -> None:
    # Also protect routers when mounted independently (CLI/tests/other apps).
    if not current_principal(request).is_root:
        raise HTTPException(status_code=403, detail=READ_ONLY_DETAIL)


def require_login(request: Request) -> Principal:
    principal = current_principal(request)
    if not principal.is_authenticated:
        raise HTTPException(status_code=401, detail=LOGIN_DETAIL)
    return principal
