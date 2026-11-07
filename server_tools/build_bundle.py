#!/usr/bin/env python3
"""只打包指定入口、说明和当前 v3 Python 源码，不包含仓库其他文件。"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
from pathlib import Path
import tarfile

HERE = Path(__file__).resolve().parent
DELIVERY_FILES = ("run_server.py", "server_config.py", "plot_intensity.py", "README.md", "requirements.txt", "environment.yml")


def build_bundle(destination):
    destination = Path(destination).expanduser().resolve()
    files = [(HERE / name, name) for name in DELIVERY_FILES]
    package = HERE.parent / "处理数据_v3" / "wrf_processing_v3"
    if not package.is_dir():
        raise FileNotFoundError(f"未找到 v3 源码：{package}")
    files.extend((path, "wrf_processing_v3/" + path.relative_to(package).as_posix())
                 for path in sorted(package.rglob("*.py")) if not path.name.startswith("._"))
    manifest = {name: hashlib.sha256(path.read_bytes()).hexdigest() for path, name in files}
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(destination, "w:gz") as archive:
        for path, name in files:
            archive.add(path, arcname="wrf_server_tools/" + name, recursive=False)
        data = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode()
        info = tarfile.TarInfo("wrf_server_tools/BUNDLE_MANIFEST.json")
        info.size = len(data)
        info.mode = 0o644
        archive.addfile(info, io.BytesIO(data))
    return destination


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=HERE.parent / "wrf_server_tools.tar.gz")
    print(build_bundle(parser.parse_args().output))
