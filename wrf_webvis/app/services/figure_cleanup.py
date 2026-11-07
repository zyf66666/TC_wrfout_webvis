"""Find and set aside figure files that no dashboard image refers to.

Nothing is deleted: unreferenced files are moved into a dated folder under
``storage_data/figures_trash`` so they can be inspected or restored, and the
user removes that folder when satisfied.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
import shutil
import time

from ..core.config import FIGURE_ROOT, STORAGE_ROOT
from ..storage.dashboard_store import DashboardStore

FIGURE_SUFFIXES = {".png", ".html", ".svg", ".pdf", ".jpg", ".jpeg"}


@dataclass
class CleanupReport:
    referenced: int = 0
    orphans: list[Path] = field(default_factory=list)
    skipped_recent: int = 0
    moved_to: Path | None = None

    @property
    def orphan_bytes(self) -> int:
        return sum(path.stat().st_size for path in self.orphans if path.exists())

    def as_dict(self) -> dict:
        return {
            "referenced_files": self.referenced,
            "unreferenced_files": len(self.orphans),
            "unreferenced_mb": round(self.orphan_bytes / 1e6, 1),
            "skipped_recent_files": self.skipped_recent,
            "moved_to": str(self.moved_to) if self.moved_to else None,
        }


# 功能说明：比对看板引用和图片目录，列出未被引用的图片；apply=True 时移到回收目录。
def cleanup_figures(
    *,
    apply: bool = False,
    min_age_hours: float = 24.0,
    figure_root: Path = FIGURE_ROOT,
    storage_root: Path = STORAGE_ROOT,
    store: DashboardStore | None = None,
) -> CleanupReport:
    store = store or DashboardStore(storage_root)
    referenced = store.referenced_figure_names()
    report = CleanupReport(referenced=len(referenced))
    cutoff = time.time() - min_age_hours * 3600
    figure_root = Path(figure_root)
    if not figure_root.is_dir():
        return report
    for path in sorted(figure_root.iterdir()):
        if not path.is_file() or path.name.startswith(".") or path.suffix.lower() not in FIGURE_SUFFIXES:
            continue
        if path.name in referenced:
            continue
        # Files from a plot job that is still running are not on a dashboard yet.
        if path.stat().st_mtime > cutoff:
            report.skipped_recent += 1
            continue
        report.orphans.append(path)
    if apply and report.orphans:
        stamp = datetime.now().strftime("%Y%m%dT%H%M%S")
        target = Path(storage_root) / "figures_trash" / stamp
        target.mkdir(parents=True, exist_ok=False)
        for path in report.orphans:
            shutil.move(str(path), str(target / path.name))
        report.moved_to = target
    return report
