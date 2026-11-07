"""导出看板 JSON、看板数据库快照和所引用的图片。

默认输出到 wrf_webvis/storage_data/backups/，不依赖仓库外的项目。
保留旧的 --research 参数名以兼容显式指定备份目录的命令。
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

WEBVIS_ROOT = Path(__file__).resolve().parent.parent
PROJECT_ROOT = WEBVIS_ROOT.parent
DEFAULT_RESEARCH = WEBVIS_ROOT / "wrf_webvis" / "storage_data" / "backups"
DEFAULT_DB = WEBVIS_ROOT / "wrf_webvis" / "storage_data" / "dashboards.sqlite"
DEFAULT_FIGURES = WEBVIS_ROOT / "wrf_webvis" / "app" / "static" / "figures"
BACKUP_DIRNAME = "_看板备份"


def _json(text):
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        return text


def _open_readonly(db: Path) -> sqlite3.Connection:
    return sqlite3.connect(db.resolve().as_uri() + "?mode=ro", uri=True)


def read_dashboards(conn: sqlite3.Connection) -> list[dict]:
    """把看板库读成可写 JSON 的结构（按看板顺序）。"""
    tables = {row[0] for row in conn.execute("select name from sqlite_master where type='table'")}
    boards = []
    # 2026-11-06：看板可以有父看板（parent_id）；旧库没有这一列时按顶层看板导出。
    columns = {row[1] for row in conn.execute("pragma table_info(dashboards)")}
    parent_column = "parent_id" if "parent_id" in columns else "null"
    for board_id, position, title, metadata, parent_id in conn.execute(
            f"select id, position, title, metadata, {parent_column} from dashboards order by position"):
        images = [
            {"position": pos, "id": image_id, **(_json(data) if isinstance(_json(data), dict) else {"data": data})}
            for image_id, pos, data in conn.execute(
                "select id, position, data from images where dashboard_id=? order by position", (board_id,))
        ]
        annotations = []
        if "image_annotations" in tables:
            annotations = [
                {"id": ann_id, "image_id": image_id, "author": author, "created_at": created, "updated_at": updated,
                 "data": _json(data)}
                for ann_id, image_id, author, data, created, updated in conn.execute(
                    "select id, image_id, author, data, created_at, updated_at from image_annotations "
                    "where dashboard_id=? order by image_id, created_at, id", (board_id,))
            ]
        deleted = []
        if "deleted_images" in tables:
            deleted = [
                {"position": pos, "id": image_id, "deleted_at": when,
                 **(_json(data) if isinstance(_json(data), dict) else {"data": data})}
                for image_id, pos, data, when in conn.execute(
                    "select id, position, data, deleted_at from deleted_images where dashboard_id=? "
                    "order by deleted_at, id", (board_id,))
            ]
        boards.append({"id": board_id, "position": position, "title": title, "parent_id": parent_id,
                       "metadata": _json(metadata),
                       "images": images, "annotations": annotations, "deleted_images": deleted})
    return boards


def _write_if_changed(path: Path, text: str) -> bool:
    if path.exists() and path.read_text(encoding="utf-8") == text:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return True


def export_json(boards: list[dict], backup_dir: Path) -> dict:
    board_dir = backup_dir / "看板"
    board_dir.mkdir(parents=True, exist_ok=True)
    changed = 0
    for board in boards:
        text = json.dumps(board, ensure_ascii=False, indent=1) + "\n"
        changed += _write_if_changed(board_dir / f"{board['id']}.json", text)
    current = {board["id"] for board in boards}
    gone = []
    for path in sorted(board_dir.glob("*.json")):
        if path.stem not in current:
            try:
                title = json.loads(path.read_text(encoding="utf-8")).get("title", "")
            except ValueError:
                title = "（JSON 读不了）"
            gone.append((path.stem, title))
    lines = ["# 看板目录（自动生成，不要手改）", "",
             "由 `wrf-webvis/server_tools/backup_research.py` 从看板库导出。每个看板一份 JSON 在 `看板/`；"
             "图片在 `figures/`（不进 git），看板库快照 `dashboards_latest.sqlite`、`dashboards_YYYY-MM.sqlite`（不进 git）。", "",
             "| 顺序 | 看板 id | 标题 | 图片 | 批注 | 已删除的图 |", "|---|---|---|---|---|---|"]
    for board in boards:
        title = str(board["title"]).replace("|", "｜")
        lines.append(f"| {board['position']} | `{board['id']}` | {title} | {len(board['images'])} | "
                     f"{len(board['annotations'])} | {len(board['deleted_images'])} |")
    if gone:
        lines += ["", "## 看板库里已经没有、但 JSON 还在的看板（可以按 JSON 恢复）", ""]
        lines += [f"- `{board_id}` {title}" for board_id, title in gone]
    _write_if_changed(backup_dir / "看板目录.md", "\n".join(lines) + "\n")
    return {"boards": len(boards), "changed": changed, "gone": gone}


def snapshot_db(conn: sqlite3.Connection, backup_dir: Path, today: dt.date) -> list[Path]:
    written = []
    latest = backup_dir / "dashboards_latest.sqlite"
    tmp = backup_dir / "dashboards_latest.sqlite.part"
    dst = sqlite3.connect(tmp)
    try:
        conn.backup(dst)
    finally:
        dst.close()
    tmp.replace(latest)
    written.append(latest)
    monthly = backup_dir / f"dashboards_{today:%Y-%m}.sqlite"
    if not monthly.exists():
        shutil.copy2(latest, monthly)
        written.append(monthly)
    return written


def mirror_figures(boards: list[dict], figures_root: Path, backup_dir: Path) -> dict:
    target_dir = backup_dir / "figures"
    target_dir.mkdir(parents=True, exist_ok=True)
    names = set()
    for board in boards:
        for item in board["images"] + board["deleted_images"]:
            path = str(item.get("image_path") or "")
            if path:
                names.add(Path(path).name)
    copied, missing = 0, []
    for name in sorted(names):
        src = figures_root / name
        dst = target_dir / name
        if not src.is_file():
            missing.append(name)
            continue
        if dst.is_file() and dst.stat().st_size == src.stat().st_size:
            continue
        shutil.copy2(src, dst)
        copied += 1
    return {"referenced": len(names), "copied": copied, "missing": missing}


def git_commit(research: Path, now: dt.datetime) -> str:
    if not (research / ".git").exists():
        return "研究/ 还不是 git 仓库，跳过提交"
    git = shutil.which("git")
    if not git:
        return "没有找到 git，跳过提交"
    status = subprocess.run([git, "-C", str(research), "status", "--porcelain"], capture_output=True, text=True)
    if status.returncode != 0:
        return f"git status 失败：{status.stderr.strip()}"
    changed = [line for line in status.stdout.splitlines() if line.strip()]
    if not changed:
        return "研究/ 没有改动，不提交"
    subprocess.run([git, "-C", str(research), "add", "-A"], check=True)
    message = f"自动备份 {now:%Y-%m-%d %H:%M}：{len(changed)} 个文件有改动"
    result = subprocess.run([git, "-C", str(research), "commit", "-q", "-m", message], capture_output=True, text=True)
    if result.returncode != 0:
        return f"git commit 失败：{(result.stderr or result.stdout).strip()}"
    return message


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="研究成果与看板备份")
    parser.add_argument("--research", type=Path, default=DEFAULT_RESEARCH)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--figures", type=Path, default=DEFAULT_FIGURES)
    parser.add_argument("--no-figures", action="store_true", help="不复制看板图片")
    parser.add_argument("--no-commit", action="store_true", help="不做 git 提交")
    args = parser.parse_args(argv)

    now = dt.datetime.now()
    print(f"[{now:%Y-%m-%d %H:%M:%S}] 研究成果与看板备份")
    if not args.research.is_dir():
        print(f"错误：找不到研究文件夹 {args.research}", file=sys.stderr)
        return 1
    backup_dir = args.research / BACKUP_DIRNAME
    backup_dir.mkdir(parents=True, exist_ok=True)
    if args.db.is_file():
        conn = _open_readonly(args.db)
        try:
            boards = read_dashboards(conn)
            info = export_json(boards, backup_dir)
            print(f"看板 JSON：{info['boards']} 个看板，{info['changed']} 个有变化")
            if info["gone"]:
                print("注意：看板库里已经没有这些看板（JSON 保留）：" + "、".join(b for b, _ in info["gone"]))
            for path in snapshot_db(conn, backup_dir, now.date()):
                print(f"看板库快照：{path.name}")
        finally:
            conn.close()
        if not args.no_figures:
            fig = mirror_figures(boards, args.figures, backup_dir)
            print(f"看板图片：引用 {fig['referenced']} 张，新复制 {fig['copied']} 张"
                  + (f"，找不到 {len(fig['missing'])} 张：{'、'.join(fig['missing'][:10])}" if fig["missing"] else ""))
    else:
        print(f"没有找到看板库 {args.db}，只做 git 提交")
    if not args.no_commit:
        print(git_commit(args.research, now))
    return 0


if __name__ == "__main__":
    sys.exit(main())
