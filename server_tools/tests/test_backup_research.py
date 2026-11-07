"""backup_research.py 的测试：只用标准库（unittest），合成一个小看板库和研究文件夹。"""
from __future__ import annotations

import json
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from server_tools import backup_research as br  # noqa: E402

SCHEMA = """
CREATE TABLE dashboards (id TEXT PRIMARY KEY, position INTEGER NOT NULL, title TEXT NOT NULL, metadata TEXT NOT NULL DEFAULT '{}');
CREATE TABLE images (dashboard_id TEXT NOT NULL, id TEXT NOT NULL, position INTEGER NOT NULL, data TEXT NOT NULL, PRIMARY KEY (dashboard_id, id));
CREATE TABLE image_annotations (id TEXT PRIMARY KEY, dashboard_id TEXT NOT NULL, image_id TEXT NOT NULL, author TEXT NOT NULL, data TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE deleted_images (dashboard_id TEXT NOT NULL, id TEXT NOT NULL, position INTEGER NOT NULL, data TEXT NOT NULL, deleted_at TEXT NOT NULL, PRIMARY KEY (dashboard_id, id));
"""


class BackupResearchTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.db = self.tmp / "dashboards.sqlite"
        self.figs = self.tmp / "figures"
        self.research = self.tmp / "研究"
        self.figs.mkdir()
        self.research.mkdir()
        (self.figs / "a1.png").write_bytes(b"png-a1")
        (self.figs / "d1.png").write_bytes(b"png-d1")
        conn = sqlite3.connect(self.db)
        conn.executescript(SCHEMA)
        conn.execute("insert into dashboards values ('b1', 1, '测试看板｜G1', '{}')")
        conn.execute("insert into images values ('b1', 'img1', 0, ?)",
                     (json.dumps({"id": "img1", "title": "G1", "image_path": "/static/figures/a1.png",
                                  "request": {"fig_type": 3, "variable": "vt"}}),))
        conn.execute("insert into image_annotations values ('n1', 'b1', 'img1', 'me', ?, 't0', 't0')",
                     (json.dumps({"shapes": [{"kind": "rect", "text": "标注"}]}),))
        conn.execute("insert into deleted_images values ('b1', 'img0', 0, ?, '2026-10-25')",
                     (json.dumps({"id": "img0", "image_path": "/static/figures/d1.png"}),))
        conn.commit()
        conn.close()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_main(self, *extra):
        return br.main(["--research", str(self.research), "--db", str(self.db), "--figures", str(self.figs), *extra])

    def test_exports_json_snapshot_and_figures_without_touching_source(self):
        before = self.db.read_bytes()
        self.assertEqual(self.run_main("--no-commit"), 0)
        backup = self.research / br.BACKUP_DIRNAME
        board = json.loads((backup / "看板" / "b1.json").read_text(encoding="utf-8"))
        self.assertEqual(board["title"], "测试看板｜G1")
        self.assertEqual(board["images"][0]["request"]["variable"], "vt")
        self.assertEqual(board["annotations"][0]["data"]["shapes"][0]["text"], "标注")
        self.assertEqual(board["deleted_images"][0]["id"], "img0")
        self.assertTrue((backup / "dashboards_latest.sqlite").is_file())
        self.assertEqual(len(list(backup.glob("dashboards_20*.sqlite"))), 1)
        self.assertEqual((backup / "figures" / "a1.png").read_bytes(), b"png-a1")
        self.assertTrue((backup / "figures" / "d1.png").is_file())
        self.assertIn("测试看板｜G1", (backup / "看板目录.md").read_text(encoding="utf-8"))
        self.assertEqual(self.db.read_bytes(), before)

    def test_vanished_dashboard_is_listed_and_its_json_kept(self):
        self.run_main("--no-commit", "--no-figures")
        conn = sqlite3.connect(self.db)
        conn.execute("delete from dashboards where id='b1'")
        conn.commit()
        conn.close()
        self.run_main("--no-commit", "--no-figures")
        backup = self.research / br.BACKUP_DIRNAME
        self.assertTrue((backup / "看板" / "b1.json").is_file())
        catalog = (backup / "看板目录.md").read_text(encoding="utf-8")
        self.assertIn("看板库里已经没有", catalog)
        self.assertIn("`b1`", catalog)

    @unittest.skipUnless(shutil.which("git"), "没有 git")
    def test_commits_only_when_something_changed(self):
        subprocess.run(["git", "init", "-q", str(self.research)], check=True)
        subprocess.run(["git", "-C", str(self.research), "config", "user.name", "test"], check=True)
        subprocess.run(["git", "-C", str(self.research), "config", "user.email", "test@example.com"], check=True)
        (self.research / ".gitignore").write_text("_看板备份/figures/\n_看板备份/*.sqlite\n", encoding="utf-8")
        self.run_main("--no-figures")
        log = subprocess.run(["git", "-C", str(self.research), "log", "--oneline"], capture_output=True, text=True).stdout
        self.assertEqual(len(log.splitlines()), 1)
        self.assertIn("自动备份", log)
        self.run_main("--no-figures")
        log = subprocess.run(["git", "-C", str(self.research), "log", "--oneline"], capture_output=True, text=True).stdout
        self.assertEqual(len(log.splitlines()), 1)


if __name__ == "__main__":
    unittest.main()
