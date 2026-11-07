"""SQLite-backed dashboard storage.

Dashboards used to live in one ``dashboards.json`` that was rewritten on every
rename, move or new image.  The data now lives in ``dashboards.sqlite``: each
image is one row, every change is a single transaction, and an interrupted
write can no longer leave a half-written file behind.

On first use an existing ``dashboards.json`` is imported once and left
untouched as a backup; later changes are not written back to it.

2026-11-06: dashboards can have one level of children (``parent_id``).  A
database created before that gets the column added once; the file is copied to
``dashboards.sqlite.bak-<time>`` first.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
import hashlib
import json
import sqlite3
import uuid
from pathlib import Path
from threading import RLock
from typing import Any, Iterator

from ..core.config import STORAGE_ROOT
from ..core.schemas import Dashboard, DashboardImage


_SCHEMA = """
CREATE TABLE IF NOT EXISTS dashboards (
    id TEXT PRIMARY KEY,
    position INTEGER NOT NULL,
    title TEXT NOT NULL,
    metadata TEXT NOT NULL DEFAULT '{}',
    parent_id TEXT
);
CREATE TABLE IF NOT EXISTS images (
    dashboard_id TEXT NOT NULL REFERENCES dashboards(id) ON DELETE CASCADE,
    id TEXT NOT NULL,
    position INTEGER NOT NULL,
    data TEXT NOT NULL,
    PRIMARY KEY (dashboard_id, id)
);
CREATE INDEX IF NOT EXISTS images_by_position ON images(dashboard_id, position);
CREATE TABLE IF NOT EXISTS store_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS image_annotations (
    id TEXT PRIMARY KEY,
    dashboard_id TEXT NOT NULL REFERENCES dashboards(id) ON DELETE CASCADE,
    image_id TEXT NOT NULL,
    author TEXT NOT NULL,
    data TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS annotations_by_image ON image_annotations(dashboard_id, image_id);
CREATE TABLE IF NOT EXISTS deleted_images (
    dashboard_id TEXT NOT NULL REFERENCES dashboards(id) ON DELETE CASCADE,
    id TEXT NOT NULL,
    position INTEGER NOT NULL,
    data TEXT NOT NULL,
    deleted_at TEXT NOT NULL,
    PRIMARY KEY (dashboard_id, id)
);
"""

_LEGACY_IMPORT_KEY = "legacy_json_imported"


class DashboardStoreError(RuntimeError):
    """The dashboard database could not be initialised safely."""


class DashboardTreeError(ValueError):
    """A parent/child change would break the two-level dashboard tree."""


class ImageTransferError(ValueError):
    """An image cannot be moved to the requested dashboard (same dashboard, or id already there)."""


class DashboardStore:
    # 功能说明：记录数据库位置；第一次读写时才建表并导入旧版 JSON。
    def __init__(self, root: str | Path = STORAGE_ROOT) -> None:
        self.root = Path(root)
        self.path = self.root / "dashboards.sqlite"
        self.legacy_json_path = self.root / "dashboards.json"
        self._lock = RLock()
        self._ready = False

    # 功能说明：读取全部仪表盘及其图片，按创建顺序返回。
    def list(self) -> list[Dashboard]:
        with self._read() as conn:
            rows = conn.execute("SELECT id, title, metadata, parent_id FROM dashboards ORDER BY position").fetchall()
            images = _images_by_dashboard(conn)
        return [_dashboard(row, images.get(row[0], [])) for row in rows]

    # 功能说明：创建带随机编号的仪表盘，写入数据库后返回；给了 parent_id 就建成那个看板的子看板。
    def create(self, title: str = "New dashboard", parent_id: str | None = None) -> Dashboard:
        dashboard = Dashboard(id=uuid.uuid4().hex[:12], title=title, parent_id=parent_id or None)
        with self._write() as conn:
            if dashboard.parent_id:
                _require_top_level_parent(conn, dashboard.parent_id)
            conn.execute(
                "INSERT INTO dashboards (id, position, title, metadata, parent_id) "
                "VALUES (?, (SELECT COALESCE(MAX(position), -1) + 1 FROM dashboards), ?, ?, ?)",
                (dashboard.id, dashboard.title, _dumps(dashboard.metadata), dashboard.parent_id),
            )
        return dashboard

    # 功能说明：把看板挂到另一个顶层看板下面（parent_id=None 表示移回顶层）。只有两级：
    # 父看板必须是顶层看板；自己已有子看板的看板不能再挂到别人下面。
    def set_parent(self, dashboard_id: str, parent_id: str | None) -> Dashboard:
        parent_id = parent_id or None
        with self._write() as conn:
            _require_dashboard(conn, dashboard_id)
            if parent_id is not None:
                if parent_id == dashboard_id:
                    raise DashboardTreeError("看板不能挂到自己下面")
                _require_top_level_parent(conn, parent_id)
                if _has_children(conn, dashboard_id):
                    raise DashboardTreeError("这个看板下面已有子看板，只支持两级，不能再挂到别的看板下面")
            conn.execute("UPDATE dashboards SET parent_id = ? WHERE id = ?", (parent_id, dashboard_id))
            return _load_dashboard(conn, dashboard_id)

    # 功能说明：侧栏用的看板清单：编号、标题、父看板、图片数（不读图片内容）。
    def summaries(self) -> list[dict[str, Any]]:
        with self._read() as conn:
            rows = conn.execute(
                "SELECT d.id, d.title, d.parent_id, (SELECT count(*) FROM images i WHERE i.dashboard_id = d.id) "
                "FROM dashboards d ORDER BY d.position").fetchall()
        return [{"id": row[0], "title": row[1], "parent_id": row[2], "image_count": row[3]} for row in rows]

    # 功能说明：返回 {看板编号: 父看板编号或 None}，权限检查用（勾选父看板＝包含子看板）。
    def parent_map(self) -> dict[str, str | None]:
        with self._read() as conn:
            return {row[0]: row[1] for row in conn.execute("SELECT id, parent_id FROM dashboards")}

    # 功能说明：按编号读取仪表盘；没有则提示错误。
    def get(self, dashboard_id: str) -> Dashboard:
        with self._read() as conn:
            return _load_dashboard(conn, dashboard_id)

    # 功能说明：删除指定仪表盘及其图片记录（图片文件本身不删除）。有子看板时拒绝，不做级联删除。
    def delete(self, dashboard_id: str) -> None:
        with self._write() as conn:
            if _has_children(conn, dashboard_id):
                raise DashboardTreeError("这个看板下面还有子看板，请先把子看板移走或删除")
            conn.execute("DELETE FROM dashboards WHERE id = ?", (dashboard_id,))

    # 功能说明：修改指定仪表盘标题并返回更新记录。
    def rename(self, dashboard_id: str, title: str) -> Dashboard:
        with self._write() as conn:
            _require_dashboard(conn, dashboard_id)
            conn.execute("UPDATE dashboards SET title = ? WHERE id = ?", (title, dashboard_id))
            return _load_dashboard(conn, dashboard_id)

    # 功能说明：把图片追加到指定仪表盘末尾并返回更新记录。
    def add_image(self, dashboard_id: str, image: DashboardImage) -> Dashboard:
        data = _model_dump(image)
        with self._write() as conn:
            _require_dashboard(conn, dashboard_id)
            conn.execute(
                "INSERT OR REPLACE INTO images (dashboard_id, id, position, data) VALUES (?, ?, "
                "(SELECT COALESCE(MAX(position), -1) + 1 FROM images WHERE dashboard_id = ?), ?)",
                (dashboard_id, data["id"], dashboard_id, _dumps(data)),
            )
            return _load_dashboard(conn, dashboard_id)

    def move_image(self, dashboard_id: str, image_id: str, before_id: str | None) -> Dashboard:
        """Move one image before *before_id* (or to the end) in one transaction."""
        with self._write() as conn:
            _require_dashboard(conn, dashboard_id)
            order = [
                row[0]
                for row in conn.execute(
                    "SELECT id FROM images WHERE dashboard_id = ? ORDER BY position", (dashboard_id,)
                )
            ]
            for target in [image_id] + ([before_id] if before_id is not None else []):
                if target not in order:
                    raise KeyError(f"Image not found: {target}")
            if image_id != before_id:
                order.remove(image_id)
                index = order.index(before_id) if before_id is not None else len(order)
                order.insert(index, image_id)
                conn.executemany(
                    "UPDATE images SET position = ? WHERE dashboard_id = ? AND id = ?",
                    [(position, dashboard_id, item) for position, item in enumerate(order)],
                )
            return _load_dashboard(conn, dashboard_id)

    # 功能说明：把一张图片从一个看板移到另一个看板（2026-11-06）：放到 before_id 之前，没给就放到末尾。
    # 图片记录、图上批注一起搬过去，图片文件不动；整个过程一个事务。返回目标看板。
    def transfer_image(self, dashboard_id: str, image_id: str, target_dashboard_id: str,
                       before_id: str | None = None) -> Dashboard:
        if target_dashboard_id == dashboard_id:
            raise ImageTransferError("图片已经在这个看板里")
        with self._write() as conn:
            _require_dashboard(conn, dashboard_id)
            _require_dashboard(conn, target_dashboard_id)
            row = conn.execute(
                "SELECT data FROM images WHERE dashboard_id = ? AND id = ?", (dashboard_id, image_id)
            ).fetchone()
            if row is None:
                raise KeyError(f"Image not found: {image_id}")
            order = [item for (item,) in conn.execute(
                "SELECT id FROM images WHERE dashboard_id = ? ORDER BY position", (target_dashboard_id,))]
            if image_id in order:
                raise ImageTransferError("目标看板里已有同一张图片（编号相同），没有移动")
            if before_id is not None and before_id not in order:
                raise KeyError(f"Image not found: {before_id}")
            image = json.loads(row[0])
            request = image.get("request")
            if isinstance(request, dict) and "dashboard_id" in request:
                request["dashboard_id"] = target_dashboard_id
            order.insert(order.index(before_id) if before_id is not None else len(order), image_id)
            conn.execute("INSERT INTO images (dashboard_id, id, position, data) VALUES (?, ?, ?, ?)",
                         (target_dashboard_id, image_id, -1, _dumps(image)))
            conn.executemany(
                "UPDATE images SET position = ? WHERE dashboard_id = ? AND id = ?",
                [(position, target_dashboard_id, item) for position, item in enumerate(order)],
            )
            conn.execute("DELETE FROM images WHERE dashboard_id = ? AND id = ?", (dashboard_id, image_id))
            conn.execute("UPDATE image_annotations SET dashboard_id = ? WHERE dashboard_id = ? AND image_id = ?",
                         (target_dashboard_id, dashboard_id, image_id))
            return _load_dashboard(conn, target_dashboard_id)

    # 功能说明：从指定仪表盘移除图片记录并返回更新记录。
    # 删除时先把图片记录放进“最近删除”（deleted_images），可以撤销；图片文件和批注都不动。
    def delete_image(self, dashboard_id: str, image_id: str) -> Dashboard:
        with self._write() as conn:
            _require_dashboard(conn, dashboard_id)
            row = conn.execute(
                "SELECT position, data FROM images WHERE dashboard_id = ? AND id = ?", (dashboard_id, image_id)
            ).fetchone()
            if row is not None:
                conn.execute(
                    "INSERT OR REPLACE INTO deleted_images (dashboard_id, id, position, data, deleted_at) VALUES (?, ?, ?, ?, ?)",
                    (dashboard_id, image_id, row[0], row[1], datetime.now().isoformat(timespec="seconds")),
                )
            conn.execute("DELETE FROM images WHERE dashboard_id = ? AND id = ?", (dashboard_id, image_id))
            return _load_dashboard(conn, dashboard_id)

    # 功能说明：列出这个看板最近删除的图片（新的在前）。
    def list_deleted_images(self, dashboard_id: str, limit: int = 50) -> list[dict[str, Any]]:
        with self._read() as conn:
            _require_dashboard(conn, dashboard_id)
            rows = conn.execute(
                "SELECT id, data, deleted_at FROM deleted_images WHERE dashboard_id = ? ORDER BY deleted_at DESC, rowid DESC LIMIT ?",
                (dashboard_id, limit),
            ).fetchall()
        result = []
        for image_id, data, deleted_at in rows:
            image = json.loads(data)
            result.append({"id": image_id, "title": image.get("title", ""), "image_path": image.get("image_path", ""),
                           "deleted_at": deleted_at})
        return result

    # 功能说明：读取一张“最近删除”里的图片记录（权限检查时看是谁出的图）。
    def get_deleted_image(self, dashboard_id: str, image_id: str) -> dict[str, Any]:
        with self._read() as conn:
            row = conn.execute("SELECT data FROM deleted_images WHERE dashboard_id = ? AND id = ?",
                               (dashboard_id, image_id)).fetchone()
        if row is None:
            raise KeyError(f"Deleted image not found: {image_id}")
        return json.loads(row[0])

    # 功能说明：撤销删除：把图片放回原来的位置。
    def restore_image(self, dashboard_id: str, image_id: str) -> Dashboard:
        with self._write() as conn:
            _require_dashboard(conn, dashboard_id)
            row = conn.execute(
                "SELECT position, data FROM deleted_images WHERE dashboard_id = ? AND id = ?", (dashboard_id, image_id)
            ).fetchone()
            if row is None:
                raise KeyError(f"Deleted image not found: {image_id}")
            if conn.execute("SELECT 1 FROM images WHERE dashboard_id = ? AND id = ?", (dashboard_id, image_id)).fetchone():
                conn.execute("DELETE FROM deleted_images WHERE dashboard_id = ? AND id = ?", (dashboard_id, image_id))
                return _load_dashboard(conn, dashboard_id)
            order = [item for (item,) in conn.execute(
                "SELECT id FROM images WHERE dashboard_id = ? ORDER BY position", (dashboard_id,))]
            # 原来排在它前面的图片数 = 原位置；位置号可能不连续，所以按旧位置比较
            before = conn.execute(
                "SELECT count(*) FROM images WHERE dashboard_id = ? AND position < ?", (dashboard_id, row[0])
            ).fetchone()[0]
            order.insert(min(before, len(order)), image_id)
            conn.execute("INSERT INTO images (dashboard_id, id, position, data) VALUES (?, ?, ?, ?)",
                         (dashboard_id, image_id, -1, row[1]))
            conn.executemany(
                "UPDATE images SET position = ? WHERE dashboard_id = ? AND id = ?",
                [(position, dashboard_id, item) for position, item in enumerate(order)],
            )
            conn.execute("DELETE FROM deleted_images WHERE dashboard_id = ? AND id = ?", (dashboard_id, image_id))
            return _load_dashboard(conn, dashboard_id)

    # 功能说明：保存图片下方的文字批注（空字符串表示清空）。
    def set_image_note(self, dashboard_id: str, image_id: str, note: str, updated_by: str | None = None) -> Dashboard:
        with self._write() as conn:
            _require_dashboard(conn, dashboard_id)
            row = conn.execute(
                "SELECT data FROM images WHERE dashboard_id = ? AND id = ?", (dashboard_id, image_id)
            ).fetchone()
            if row is None:
                raise KeyError(f"Image not found: {image_id}")
            image = json.loads(row[0])
            image["note"] = note
            image["note_updated_at"] = datetime.now().isoformat(timespec="seconds") if note else ""
            if updated_by:
                image["note_updated_by"] = updated_by
            conn.execute("UPDATE images SET data = ? WHERE dashboard_id = ? AND id = ?",
                         (_dumps(image), dashboard_id, image_id))
            return _load_dashboard(conn, dashboard_id)

    # 功能说明：修改指定图片标题，同时同步元数据里的标题，返回更新记录。
    def rename_image(self, dashboard_id: str, image_id: str, title: str) -> Dashboard:
        with self._write() as conn:
            _require_dashboard(conn, dashboard_id)
            row = conn.execute(
                "SELECT data FROM images WHERE dashboard_id = ? AND id = ?", (dashboard_id, image_id)
            ).fetchone()
            if row is None:
                raise KeyError(f"Image not found: {image_id}")
            image = json.loads(row[0])
            image["title"] = title
            metadata = image.setdefault("metadata", {})
            if isinstance(metadata, dict):
                metadata["title"] = title
            conn.execute(
                "UPDATE images SET data = ? WHERE dashboard_id = ? AND id = ?",
                (_dumps(image), dashboard_id, image_id),
            )
            return _load_dashboard(conn, dashboard_id)

    # 功能说明：读取一张图片的完整记录（含绘图结果元数据）。
    def get_image(self, dashboard_id: str, image_id: str) -> dict[str, Any]:
        with self._read() as conn:
            _require_dashboard(conn, dashboard_id)
            row = conn.execute(
                "SELECT data FROM images WHERE dashboard_id = ? AND id = ?", (dashboard_id, image_id)
            ).fetchone()
        if row is None:
            raise KeyError(f"Image not found: {image_id}")
        return json.loads(row[0])

    # ------------------------------------------------------------------ 图片批注
    # 一条批注记录 = 一次保存的一组形状（箭头、框、文字……）和一段说明；author 为 user 或 ai。
    def list_annotations(self, dashboard_id: str, image_id: str | None = None) -> list[dict[str, Any]]:
        with self._read() as conn:
            _require_dashboard(conn, dashboard_id)
            if image_id is None:
                rows = conn.execute(
                    "SELECT id, image_id, author, data, created_at, updated_at FROM image_annotations "
                    "WHERE dashboard_id = ? ORDER BY created_at, id", (dashboard_id,)).fetchall()
            else:
                rows = conn.execute(
                    "SELECT id, image_id, author, data, created_at, updated_at FROM image_annotations "
                    "WHERE dashboard_id = ? AND image_id = ? ORDER BY created_at, id", (dashboard_id, image_id)).fetchall()
        return [_annotation(dashboard_id, row) for row in rows]

    def add_annotation(self, dashboard_id: str, image_id: str, author: str, shapes: list[dict[str, Any]],
                       note: str = "", created_by: str | None = None) -> dict[str, Any]:
        now = datetime.now().isoformat(timespec="seconds")
        annotation_id = uuid.uuid4().hex[:12]
        with self._write() as conn:
            _require_image(conn, dashboard_id, image_id)
            conn.execute(
                "INSERT INTO image_annotations (id, dashboard_id, image_id, author, data, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (annotation_id, dashboard_id, image_id, author,
                 _dumps({"shapes": shapes, "note": note, **({"created_by": created_by} if created_by else {})}), now, now),
            )
            row = conn.execute("SELECT id, image_id, author, data, created_at, updated_at FROM image_annotations WHERE id = ?",
                               (annotation_id,)).fetchone()
        return _annotation(dashboard_id, row)

    # 功能说明：读取一条批注（权限检查时看它是谁写的）。
    def get_annotation(self, dashboard_id: str, image_id: str, annotation_id: str) -> dict[str, Any]:
        with self._read() as conn:
            row = conn.execute(
                "SELECT id, image_id, author, data, created_at, updated_at FROM image_annotations "
                "WHERE id = ? AND dashboard_id = ? AND image_id = ?", (annotation_id, dashboard_id, image_id)).fetchone()
        if row is None:
            raise KeyError(f"Annotation not found: {annotation_id}")
        return _annotation(dashboard_id, row)

    def update_annotation(self, dashboard_id: str, image_id: str, annotation_id: str, *,
                          shapes: list[dict[str, Any]] | None = None, note: str | None = None) -> dict[str, Any]:
        now = datetime.now().isoformat(timespec="seconds")
        with self._write() as conn:
            row = conn.execute(
                "SELECT data FROM image_annotations WHERE id = ? AND dashboard_id = ? AND image_id = ?",
                (annotation_id, dashboard_id, image_id)).fetchone()
            if row is None:
                raise KeyError(f"Annotation not found: {annotation_id}")
            data = json.loads(row[0])
            if shapes is not None:
                data["shapes"] = shapes
            if note is not None:
                data["note"] = note
            conn.execute("UPDATE image_annotations SET data = ?, updated_at = ? WHERE id = ?",
                         (_dumps(data), now, annotation_id))
            row = conn.execute("SELECT id, image_id, author, data, created_at, updated_at FROM image_annotations WHERE id = ?",
                               (annotation_id,)).fetchone()
        return _annotation(dashboard_id, row)

    def delete_annotation(self, dashboard_id: str, image_id: str, annotation_id: str) -> None:
        with self._write() as conn:
            deleted = conn.execute(
                "DELETE FROM image_annotations WHERE id = ? AND dashboard_id = ? AND image_id = ?",
                (annotation_id, dashboard_id, image_id)).rowcount
        if not deleted:
            raise KeyError(f"Annotation not found: {annotation_id}")

    # 功能说明：看板的“版本号”：图片或批注有任何变化时都会改变，网页据此自动刷新。
    def revision(self, dashboard_id: str) -> str:
        with self._read() as conn:
            row = conn.execute("SELECT title FROM dashboards WHERE id = ?", (dashboard_id,)).fetchone()
            if row is None:
                raise KeyError(f"Dashboard not found: {dashboard_id}")
            images = conn.execute(
                "SELECT group_concat(id || ':' || length(data), ',') FROM (SELECT id, data FROM images WHERE dashboard_id = ? ORDER BY position)",
                (dashboard_id,)).fetchone()[0] or ""
            notes = conn.execute(
                "SELECT count(*), COALESCE(MAX(updated_at), '') FROM image_annotations WHERE dashboard_id = ?",
                (dashboard_id,)).fetchone()
        return hashlib.sha1(f"{row[0]}|{images}|{notes[0]}|{notes[1]}".encode("utf-8")).hexdigest()[:16]

    # 功能说明：返回所有图片记录引用到的静态文件名，供清理未使用图片时比对。
    def referenced_figure_names(self) -> set[str]:
        with self._read() as conn:
            # 最近删除的图片也算“被引用”，这样清理图片文件时不会把可撤销的图移走
            rows = conn.execute("SELECT data FROM images UNION ALL SELECT data FROM deleted_images").fetchall()
        names: set[str] = set()
        for (data,) in rows:
            _collect_figure_names(json.loads(data), names)
        return names

    # ------------------------------------------------------------------ internals

    def _connect(self) -> sqlite3.Connection:
        self.root.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 30000")
        return conn

    # 功能说明：首次使用时建表，开启 WAL，并在数据库为空时导入旧版 JSON。
    def _ensure_ready(self) -> None:
        if self._ready:
            return
        conn = self._connect()
        try:
            conn.execute("PRAGMA journal_mode = WAL")
            existed = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'dashboards'").fetchone() is not None
            conn.executescript(_SCHEMA)
            columns = {row[1] for row in conn.execute("PRAGMA table_info(dashboards)")}
            if "parent_id" not in columns:
                if existed:
                    self._backup_before_migration(conn)
                conn.execute("ALTER TABLE dashboards ADD COLUMN parent_id TEXT")
            conn.execute("BEGIN IMMEDIATE")
            try:
                imported = conn.execute(
                    "SELECT 1 FROM store_meta WHERE key = ?", (_LEGACY_IMPORT_KEY,)
                ).fetchone()
                if imported is None:
                    source = self._import_legacy_json(conn)
                    conn.execute(
                        "INSERT INTO store_meta (key, value) VALUES (?, ?)",
                        (_LEGACY_IMPORT_KEY, json.dumps({"source": source, "at": datetime.now().isoformat()})),
                    )
                conn.execute("COMMIT")
            except BaseException:
                conn.execute("ROLLBACK")
                raise
        finally:
            conn.close()
        self._ready = True

    # 功能说明：给旧库加列之前，先用 SQLite 备份接口完整复制一份（含 WAL 里的内容）。
    def _backup_before_migration(self, conn: sqlite3.Connection) -> Path:
        target = self.root / f"{self.path.name}.bak-{datetime.now():%Y%m%d-%H%M%S}"
        backup = sqlite3.connect(target)
        try:
            conn.backup(backup)
            backup.execute("PRAGMA journal_mode = DELETE")  # 备份是单个独立文件，打开时不生成 -wal/-shm
        finally:
            backup.close()
        return target

    def _import_legacy_json(self, conn: sqlite3.Connection) -> str | None:
        if not self.legacy_json_path.exists():
            return None
        if conn.execute("SELECT 1 FROM dashboards LIMIT 1").fetchone() is not None:
            return None
        try:
            dashboards = json.loads(self.legacy_json_path.read_text(encoding="utf-8"))
            if not isinstance(dashboards, list):
                raise ValueError("顶层不是列表")
            for position, raw in enumerate(dashboards):
                dashboard = Dashboard(**raw)
                conn.execute(
                    "INSERT INTO dashboards (id, position, title, metadata) VALUES (?, ?, ?, ?)",
                    (dashboard.id, position, dashboard.title, _dumps(dashboard.metadata)),
                )
                conn.executemany(
                    "INSERT OR REPLACE INTO images (dashboard_id, id, position, data) VALUES (?, ?, ?, ?)",
                    [
                        (dashboard.id, image.id, index, _dumps(_model_dump(image)))
                        for index, image in enumerate(dashboard.images)
                    ],
                )
        except (OSError, ValueError, TypeError, sqlite3.IntegrityError) as exc:
            raise DashboardStoreError(
                f"无法导入旧版看板文件 {self.legacy_json_path}：{exc}。"
                "原文件未改动；修复或移走该文件后重启服务。"
            ) from exc
        return str(self.legacy_json_path)

    @contextmanager
    def _read(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            self._ensure_ready()
            conn = self._connect()
        try:
            yield conn
        finally:
            conn.close()

    # 功能说明：以独占写事务执行修改；出错时整体回滚，不会留下部分修改。
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


def _require_dashboard(conn: sqlite3.Connection, dashboard_id: str) -> None:
    if conn.execute("SELECT 1 FROM dashboards WHERE id = ?", (dashboard_id,)).fetchone() is None:
        raise KeyError(f"Dashboard not found: {dashboard_id}")


def _has_children(conn: sqlite3.Connection, dashboard_id: str) -> bool:
    return conn.execute("SELECT 1 FROM dashboards WHERE parent_id = ? LIMIT 1", (dashboard_id,)).fetchone() is not None


def _require_top_level_parent(conn: sqlite3.Connection, parent_id: str) -> None:
    row = conn.execute("SELECT parent_id FROM dashboards WHERE id = ?", (parent_id,)).fetchone()
    if row is None:
        raise KeyError(f"Dashboard not found: {parent_id}")
    if row[0]:
        raise DashboardTreeError("只支持两级：不能挂到一个子看板下面")


def _require_image(conn: sqlite3.Connection, dashboard_id: str, image_id: str) -> None:
    _require_dashboard(conn, dashboard_id)
    if conn.execute("SELECT 1 FROM images WHERE dashboard_id = ? AND id = ?", (dashboard_id, image_id)).fetchone() is None:
        raise KeyError(f"Image not found: {image_id}")


def _annotation(dashboard_id: str, row: tuple) -> dict[str, Any]:
    annotation_id, image_id, author, data, created_at, updated_at = row
    payload = json.loads(data)
    return {"id": annotation_id, "dashboard_id": dashboard_id, "image_id": image_id, "author": author,
            "created_by": payload.get("created_by") or "",
            "shapes": payload.get("shapes") or [], "note": payload.get("note") or "",
            "created_at": created_at, "updated_at": updated_at}


def _load_dashboard(conn: sqlite3.Connection, dashboard_id: str) -> Dashboard:
    row = conn.execute("SELECT id, title, metadata, parent_id FROM dashboards WHERE id = ?", (dashboard_id,)).fetchone()
    if row is None:
        raise KeyError(f"Dashboard not found: {dashboard_id}")
    images = [
        json.loads(data)
        for (data,) in conn.execute(
            "SELECT data FROM images WHERE dashboard_id = ? ORDER BY position", (dashboard_id,)
        )
    ]
    return _dashboard(row, images)


def _images_by_dashboard(conn: sqlite3.Connection) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for dashboard_id, data in conn.execute("SELECT dashboard_id, data FROM images ORDER BY dashboard_id, position"):
        grouped.setdefault(dashboard_id, []).append(json.loads(data))
    return grouped


def _dashboard(row: tuple, images: list[dict[str, Any]]) -> Dashboard:
    dashboard_id, title, metadata, parent_id = row
    return Dashboard(id=dashboard_id, title=title, images=images, metadata=json.loads(metadata), parent_id=parent_id)


def _collect_figure_names(value: Any, names: set[str]) -> None:
    if isinstance(value, dict):
        for item in value.values():
            _collect_figure_names(item, names)
    elif isinstance(value, list):
        for item in value:
            _collect_figure_names(item, names)
    elif isinstance(value, str) and "/figures/" in value:
        names.add(value.rsplit("/figures/", 1)[1].split("?", 1)[0])


def _dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


# 功能说明：把模型对象转换为普通字典，兼容新旧两种模型接口。
def _model_dump(model: Any) -> dict[str, Any]:
    if hasattr(model, "model_dump"):
        return model.model_dump()
    if hasattr(model, "dict"):
        return model.dict()
    return dict(model)
