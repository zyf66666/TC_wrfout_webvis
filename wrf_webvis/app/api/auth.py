"""登录、退出、改自己的密码（2026-11-06）。会话存在 storage_data/users.sqlite，见 storage/user_store.py。"""
from __future__ import annotations

import time
from collections import defaultdict, deque
from threading import Lock

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel, Field

from ..core.access import SESSION_COOKIE, AccessPolicy, current_principal, require_login, session_token
from ..core.config import STATIC_ROOT
from ..storage import user_store as user_store_module
from ..storage.user_store import SESSION_SECONDS, UserError, principal_from_user

router = APIRouter(tags=["auth"])

# 同一“用户名 + 来源”10 分钟内最多失败 5 次。
MAX_FAILURES = 5
FAILURE_WINDOW = 600
_failures: dict[tuple[str, str], deque] = defaultdict(deque)
_failures_lock = Lock()


class LoginPayload(BaseModel):
    username: str = Field(default="", max_length=64)
    password: str = Field(default="", max_length=256)


class PasswordPayload(BaseModel):
    old_password: str = Field(default="", max_length=256)
    new_password: str = Field(default="", max_length=256)


def _source(request: Request) -> str:
    address, _ = AccessPolicy.from_environment().client(request.scope)
    return str(address or "unknown")


def _too_many(key: tuple[str, str]) -> bool:
    now = time.time()
    with _failures_lock:
        attempts = _failures[key]
        while attempts and attempts[0] < now - FAILURE_WINDOW:
            attempts.popleft()
        return len(attempts) >= MAX_FAILURES


def _record_failure(key: tuple[str, str]) -> None:
    with _failures_lock:
        _failures[key].append(time.time())


def _secure(request: Request) -> bool:
    return request.headers.get("x-forwarded-proto") == "https" or request.url.scheme == "https"


def safe_next(value: str | None) -> str:
    """登录后跳转的地址只允许本站路径，防止被拿去跳到别的网站。"""
    value = str(value or "")
    if not value.startswith("/") or value.startswith("//") or "\\" in value or value.startswith("/login"):
        return "/"
    return value


# 功能说明：登录页。已登录（或本机访问）时直接跳回原来要去的页面。
@router.get("/login", include_in_schema=False)
def login_page(request: Request, next: str | None = None):
    if current_principal(request).is_authenticated:
        return RedirectResponse(safe_next(next), status_code=303)
    return FileResponse(STATIC_ROOT / "login.html", headers={"Cache-Control": "no-store"})


@router.post("/api/auth/login")
def login(payload: LoginPayload, request: Request):
    username = payload.username.strip()
    key = (username.lower(), _source(request))
    if _too_many(key):
        raise HTTPException(status_code=429, detail={"code": "TOO_MANY", "message": "失败次数太多，请 10 分钟后再试"})
    store = user_store_module.store
    if not username or not store.verify(username, payload.password):
        _record_failure(key)
        raise HTTPException(status_code=401, detail={"code": "BAD_LOGIN", "message": "用户名或密码不对"})
    with _failures_lock:
        _failures.pop(key, None)
    token = store.create_session(username)
    principal = principal_from_user(store.get_user(username))
    response = JSONResponse(principal.as_access(), headers={"Cache-Control": "no-store"})
    response.set_cookie(SESSION_COOKIE, token, max_age=SESSION_SECONDS, httponly=True, samesite="lax",
                        secure=_secure(request), path="/")
    return response


@router.post("/api/auth/logout")
def logout(request: Request):
    user_store_module.store.delete_session(session_token(request.scope))
    response = JSONResponse({"status": "logged_out"}, headers={"Cache-Control": "no-store"})
    response.delete_cookie(SESSION_COOKIE, path="/", httponly=True, samesite="lax", secure=_secure(request))
    return response


@router.get("/api/auth/me")
def me(request: Request):
    return JSONResponse(require_login(request).as_access(), headers={"Cache-Control": "no-store"})


# 功能说明：登录用户改自己的密码（要输入旧密码）；改完其他设备上的登录失效，当前设备保持登录。
@router.post("/api/auth/password")
def change_password(payload: PasswordPayload, request: Request):
    principal = require_login(request)
    if principal.is_local:
        raise HTTPException(status_code=400, detail="本机访问没有登录账号；请在“账号管理”里改账号密码")
    store = user_store_module.store
    if not store.verify(principal.username, payload.old_password):
        raise HTTPException(status_code=403, detail={"code": "BAD_LOGIN", "message": "旧密码不对"})
    try:
        store.set_password(principal.username, payload.new_password)
    except UserError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    token = store.create_session(principal.username)
    response = JSONResponse({"status": "changed"}, headers={"Cache-Control": "no-store"})
    response.set_cookie(SESSION_COOKIE, token, max_age=SESSION_SECONDS, httponly=True, samesite="lax",
                        secure=_secure(request), path="/")
    return response
