from __future__ import annotations

import os
import time

from wrf_webvis.app.core.schemas import DashboardImage
from wrf_webvis.app.services.figure_cleanup import cleanup_figures
from wrf_webvis.app.storage.dashboard_store import DashboardStore


def _touch(path, *, age_hours: float) -> None:
    path.write_bytes(b"x" * 10)
    stamp = time.time() - age_hours * 3600
    os.utime(path, (stamp, stamp))


# 功能说明：只移走旧的、未被引用的图片；被引用、刚生成或非图片文件保持原位。
def test_cleanup_moves_only_old_unreferenced_figures(tmp_path) -> None:
    figures = tmp_path / "figures"
    storage = tmp_path / "storage"
    figures.mkdir()
    store = DashboardStore(storage)
    dashboard = store.create("d")
    store.add_image(
        dashboard.id,
        DashboardImage(id="1", title="t", image_path="/static/figures/kept.png", request={},
                       metadata={"html_static_path": "/static/figures/kept.html"}),
    )
    for name in ["kept.png", "kept.html", "old.png", "notes.txt"]:
        _touch(figures / name, age_hours=48)
    _touch(figures / "fresh.png", age_hours=1)

    preview = cleanup_figures(figure_root=figures, storage_root=storage)
    assert [p.name for p in preview.orphans] == ["old.png"]
    assert preview.skipped_recent == 1
    assert (figures / "old.png").exists()

    applied = cleanup_figures(apply=True, figure_root=figures, storage_root=storage)
    assert not (figures / "old.png").exists()
    assert (applied.moved_to / "old.png").exists()
    assert sorted(p.name for p in figures.iterdir()) == ["fresh.png", "kept.html", "kept.png", "notes.txt"]
