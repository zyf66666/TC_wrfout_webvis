"""账号与登录会话（2026-11-06）。

账号存在 ``storage_data/users.sqlite``（不进 git，勿外传）：

- ``users``：用户名、密码哈希（标准库 scrypt，加盐）、角色（root / user）、是否可批注、
  是否可看绘图设置、是否可出图（11-06 下午加）、可见页面、可见看板（看板编号列表，或 ``"*"`` 表示全部）、是否停用。
- ``sessions``：登录会话。只存 token 的 sha256，浏览器 cookie 里是 token 本身。

谁能看什么由 ``core/access.py`` 决定；这里只管存取与校验。
"""
from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from threading import RLock
from typing import Any, Iterable, Iterator, Optional

from ..core.config import STORAGE_ROOT


ROLES = ("root", "user")
# 非 root 账号可以额外打开的页面（看板总是可以打开，看哪些看板另外勾选）。
PAGES = {"data": "数据管理（只读）"}
ALL_DASHBOARDS = "*"
USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9_.-]{1,32}$")
MIN_PASSWORD_LENGTH = 6
SESSION_SECONDS = 30 * 24 * 3600
LOCAL_USERNAME = "本机"

_SCRYPT_N, _SCRYPT_R, _SCRYPT_P = 2 ** 14, 8, 1

_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    username TEXT PRIMARY KEY,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL,
    can_annotate INTEGER NOT NULL DEFAULT 0,
    can_view_plot_settings INTEGER NOT NULL DEFAULT 0,
    can_plot INTEGER NOT NULL DEFAULT 0,
    pages TEXT NOT NULL DEFAULT '[]',
    dashboards TEXT NOT NULL DEFAULT '[]',
    disabled INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,
    username TEXT NOT NULL REFERENCES users(username) ON DELETE CASCADE ON UPDATE CASCADE,
    created_at REAL NOT NULL,
    expires_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS sessions_by_user ON sessions(username);
"""


class UserError(ValueError):
    """账号输入不合法（用户名、密码、角色等）。"""


@dataclass(frozen=True)
class Principal:
    """一次请求是谁、能做什么。"""

    username: str
    role: str  # "root" / "user" / "anonymous"
    can_annotate: bool = False
    # 看得到每张图的出图参数（只读打开绘图界面）；可出图＝能用绘图界面往自己能看的看板里加图（含看参数）。
    can_view_plot_settings: bool = False
    can_plot: bool = False
    pages: frozenset = field(default_factory=frozenset)
    # None 表示全部看板；否则是允许的看板编号（勾选父看板＝包含其子看板）。
    dashboards: Optional[frozenset] = frozenset()
    is_local: bool = False

    @property
    def is_root(self) -> bool:
        return self.role == "root"

    @property
    def is_authenticated(self) -> bool:
        return self.role in ROLES

    def can_see_page(self, page: str) -> bool:
        return self.is_root or page in self.pages

    def can_view_dashboard(self, dashboard_id: str, parent_id: str | None = None) -> bool:
        if self.is_root or self.dashboards is None:
            return self.is_authenticated
        return dashboard_id in self.dashboards or bool(parent_id and parent_id in self.dashboards)

    @property
    def can_see_plot_settings(self) -> bool:
        return self.is_root or (self.is_authenticated and (self.can_view_plot_settings or self.can_plot))

    @property
    def may_plot(self) -> bool:
        return self.is_root or (self.is_authenticated and self.can_plot)

    def can_annotate_dashboard(self, dashboard_id: str, parent_id: str | None = None) -> bool:
        if self.is_root:
            return True
        return self.can_annotate and self.can_view_dashboard(dashboard_id, parent_id)

    def as_access(self) -> dict[str, Any]:
        """``/api/access`` 的返回值；保留旧字段 read_only / can_write（root 才可写）。"""
        return {
            "authenticated": self.is_authenticated,
            "username": self.username if self.is_authenticated else "",
            "role": self.role,
            "is_local": self.is_local,
            "read_only": not self.is_root,
            "can_write": self.is_root,
            "can_annotate": self.is_root or (self.is_authenticated and self.can_annotate),
            "can_view_plot_settings": self.can_see_plot_settings,
            "can_plot": self.may_plot,
            "pages": sorted(PAGES) if self.is_root else sorted(self.pages),
        }


ANONYMOUS = Principal(username="", role="anonymous")
LOCAL_ROOT = Principal(username=LOCAL_USERNAME, role="root", can_annotate=True, can_view_plot_settings=True,
                       can_plot=True, dashboards=None, is_local=True)


# ---------------------------------------------------------------------- 密码
def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P, dklen=32)
    return f"scrypt${_SCRYPT_N}${_SCRYPT_R}${_SCRYPT_P}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, n, r, p, salt, expected = stored.split("$")
        if scheme != "scrypt":
            return False
        digest = hashlib.scrypt(password.encode("utf-8"), salt=bytes.fromhex(salt), n=int(n), r=int(r), p=int(p),
                                dklen=len(expected) // 2)
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(digest.hex(), expected)


# 校验失败时也算一次哈希，避免靠响应时间判断用户名是否存在。
_DUMMY_HASH = hash_password(secrets.token_hex(8))


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _check_username(username: str) -> str:
    username = str(username or "").strip()
    if not USERNAME_PATTERN.fullmatch(username):
        raise UserError("用户名只能用字母、数字、下划线、点和短横线，1–32 个字符")
    return username


def _check_password(password: str) -> str:
    password = str(password or "")
    if len(password) < MIN_PASSWORD_LENGTH:
        raise UserError(f"密码至少 {MIN_PASSWORD_LENGTH} 位")
    if len(password) > 256:
        raise UserError("密码太长")
    return password


def _check_role(role: str) -> str:
    if role not in ROLES:
        raise UserError("角色只能是 root 或 user")
    return role


def _check_pages(pages: Iterable[str] | None) -> list[str]:
    result = sorted({str(page) for page in (pages or [])})
    unknown = [page for page in result if page not in PAGES]
    if unknown:
        raise UserError(f"未知页面：{', '.join(unknown)}")
    return result


def _check_dashboards(dashboards: Any) -> Any:
    if dashboards == ALL_DASHBOARDS:
        return ALL_DASHBOARDS
    if dashboards is None:
        return []
    if not isinstance(dashboards, (list, tuple, set, frozenset)):
        raise UserError("可见看板要写成看板编号列表，或 \"*\" 表示全部")
    return sorted({str(item) for item in dashboards if str(item).strip()})


class UserStore:
    def __init__(self, root: str | Path = STORAGE_ROOT) -> None:
        self.root = Path(root)
        self.path = self.root / "users.sqlite"
        self._lock = RLock()
        self._ready = False

    # ------------------------------------------------------------------ 账号
    def list_users(self) -> list[dict[str, Any]]:
        with self._read() as conn:
            rows = conn.execute(f"SELECT {_USER_COLUMNS} FROM users u ORDER BY u.role != 'root', u.username").fetchall()
        return [_user_dict(row) for row in rows]

    def get_user(self, username: str) -> dict[str, Any]:
        with self._read() as conn:
            row = conn.execute(f"SELECT {_USER_COLUMNS} FROM users u WHERE u.username = ?", (username,)).fetchone()
        if row is None:
            raise KeyError(f"没有这个账号：{username}")
        return _user_dict(row)

    def has_users(self) -> bool:
        with self._read() as conn:
            return conn.execute("SELECT 1 FROM users LIMIT 1").fetchone() is not None

    def create_user(self, username: str, password: str, role: str = "user", *, can_annotate: bool = False,
                    pages: Iterable[str] | None = None, dashboards: Any = None, can_view_plot_settings: bool = False,
                    can_plot: bool = False) -> dict[str, Any]:
        username, password, role = _check_username(username), _check_password(password), _check_role(role)
        now = _now()
        with self._write() as conn:
            if conn.execute("SELECT 1 FROM users WHERE username = ?", (username,)).fetchone():
                raise UserError(f"账号 {username} 已存在")
            conn.execute(
                "INSERT INTO users (username, password_hash, role, can_annotate, can_view_plot_settings, can_plot, pages, "
                "dashboards, disabled, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?)",
                (username, hash_password(password), role, int(bool(can_annotate)),
                 int(bool(can_view_plot_settings or can_plot)), int(bool(can_plot)), json.dumps(_check_pages(pages)),
                 json.dumps(_check_dashboards(dashboards)), now, now),
            )
        return self.get_user(username)

    def update_user(self, username: str, *, role: str | None = None, can_annotate: bool | None = None,
                    pages: Iterable[str] | None = None, dashboards: Any = None, disabled: bool | None = None,
                    set_dashboards: bool = False, can_view_plot_settings: bool | None = None,
                    can_plot: bool | None = None) -> dict[str, Any]:
        changes: dict[str, Any] = {}
        if role is not None:
            changes["role"] = _check_role(role)
        if can_annotate is not None:
            changes["can_annotate"] = int(bool(can_annotate))
        if can_view_plot_settings is not None:
            changes["can_view_plot_settings"] = int(bool(can_view_plot_settings))
        if can_plot is not None:
            changes["can_plot"] = int(bool(can_plot))
            if can_plot:
                changes["can_view_plot_settings"] = 1  # 能出图就一定能看参数
        if pages is not None:
            changes["pages"] = json.dumps(_check_pages(pages))
        if set_dashboards or dashboards is not None:
            changes["dashboards"] = json.dumps(_check_dashboards(dashboards))
        if disabled is not None:
            changes["disabled"] = int(bool(disabled))
        with self._write() as conn:
            if conn.execute("SELECT 1 FROM users WHERE username = ?", (username,)).fetchone() is None:
                raise KeyError(f"没有这个账号：{username}")
            if changes:
                changes["updated_at"] = _now()
                columns = ", ".join(f"{key} = ?" for key in changes)
                conn.execute(f"UPDATE users SET {columns} WHERE username = ?", (*changes.values(), username))
            if disabled:
                conn.execute("DELETE FROM sessions WHERE username = ?", (username,))
        return self.get_user(username)

    def set_password(self, username: str, password: str, *, revoke_sessions: bool = True) -> None:
        password = _check_password(password)
        with self._write() as conn:
            updated = conn.execute("UPDATE users SET password_hash = ?, updated_at = ? WHERE username = ?",
                                   (hash_password(password), _now(), username)).rowcount
            if not updated:
                raise KeyError(f"没有这个账号：{username}")
            if revoke_sessions:
                conn.execute("DELETE FROM sessions WHERE username = ?", (username,))

    def delete_user(self, username: str) -> None:
        with self._write() as conn:
            conn.execute("DELETE FROM sessions WHERE username = ?", (username,))
            if not conn.execute("DELETE FROM users WHERE username = ?", (username,)).rowcount:
                raise KeyError(f"没有这个账号：{username}")

    def verify(self, username: str, password: str) -> bool:
        with self._read() as conn:
            row = conn.execute("SELECT password_hash, disabled FROM users WHERE username = ?",
                               (str(username or "").strip(),)).fetchone()
        if row is None:
            verify_password(password, _DUMMY_HASH)
            return False
        return verify_password(password, row[0]) and not row[1]

    # ------------------------------------------------------------------ 会话
    def create_session(self, username: str, seconds: int = SESSION_SECONDS) -> str:
        token = secrets.token_urlsafe(32)
        now = time.time()
        with self._write() as conn:
            conn.execute("DELETE FROM sessions WHERE expires_at < ?", (now,))
            conn.execute("INSERT INTO sessions (token_hash, username, created_at, expires_at) VALUES (?, ?, ?, ?)",
                         (_token_hash(token), username, now, now + seconds))
        return token

    def delete_session(self, token: str | None) -> None:
        if not token:
            return
        with self._write() as conn:
            conn.execute("DELETE FROM sessions WHERE token_hash = ?", (_token_hash(token),))

    def principal_for_token(self, token: str | None) -> Principal | None:
        if not token or len(token) > 200:
            return None
        if not self.path.exists():
            return None
        with self._read() as conn:
            row = conn.execute(
                f"SELECT {_USER_COLUMNS} FROM sessions s JOIN users u ON u.username = s.username "
                "WHERE s.token_hash = ? AND s.expires_at > ? AND u.disabled = 0",
                (_token_hash(token), time.time()),
            ).fetchone()
        return principal_from_user(_user_dict(row)) if row else None

    # ------------------------------------------------------------------ internals
    def _connect(self) -> sqlite3.Connection:
        self.root.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 30000")
        return conn

    def _ensure_ready(self) -> None:
        if self._ready:
            return
        conn = self._connect()
        try:
            conn.execute("PRAGMA journal_mode = WAL")
            conn.executescript(_SCHEMA)
            # 11-06 上午建的库没有这两列，补上（默认 0 ＝没有权限）
            columns = {row[1] for row in conn.execute("PRAGMA table_info(users)")}
            for column in ("can_view_plot_settings", "can_plot"):
                if column not in columns:
                    conn.execute(f"ALTER TABLE users ADD COLUMN {column} INTEGER NOT NULL DEFAULT 0")
        finally:
            conn.close()
        try:
            self.path.chmod(0o600)
        except OSError:
            pass
        self._ready = True

    @contextmanager
    def _read(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            self._ensure_ready()
        conn = self._connect()
        try:
            yield conn
        finally:
            conn.close()

    @contextmanager
    def _write(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            self._ensure_ready()
            conn = self._connect()
            try:
                conn.execute("BEGIN IMMEDIATE")
                try:
                    yield conn
                    conn.execute("COMMIT")
                except BaseException:
                    conn.execute("ROLLBACK")
                    raise
            finally:
                conn.close()


_USER_COLUMNS = ("u.username, u.role, u.can_annotate, u.pages, u.dashboards, u.disabled, u.created_at, u.updated_at, "
                 "u.can_view_plot_settings, u.can_plot")


def _user_dict(row: tuple) -> dict[str, Any]:
    (username, role, can_annotate, pages, dashboards, disabled, created_at, updated_at,
     can_view_plot_settings, can_plot) = row
    return {
        "username": username,
        "role": role,
        "can_annotate": bool(can_annotate),
        "can_view_plot_settings": bool(can_view_plot_settings or can_plot),
        "can_plot": bool(can_plot),
        "pages": json.loads(pages or "[]"),
        "dashboards": json.loads(dashboards or "[]"),
        "disabled": bool(disabled),
        "created_at": created_at,
        "updated_at": updated_at,
    }


def principal_from_user(user: dict[str, Any]) -> Principal:
    dashboards = user.get("dashboards")
    return Principal(
        username=user["username"],
        role=user["role"],
        can_annotate=bool(user.get("can_annotate")),
        can_view_plot_settings=bool(user.get("can_view_plot_settings")),
        can_plot=bool(user.get("can_plot")),
        pages=frozenset(user.get("pages") or []),
        dashboards=None if (user["role"] == "root" or dashboards == ALL_DASHBOARDS) else frozenset(dashboards or []),
    )


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


# 网页进程里共用的一份；测试里用 monkeypatch 换成临时目录。
store = UserStore()
