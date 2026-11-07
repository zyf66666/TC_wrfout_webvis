from __future__ import annotations

import json
import threading

import numpy as np

from wrf_webvis.app.core.paths import atomic_write_json
from wrf_processing.processing_utils import atomic_save


# 功能说明：让两个网页任务同时保存同一数组缓存，确认最终文件可读取且不会留下半写入内容。
def test_atomic_save_allows_two_web_jobs_to_write_the_same_cache_path(tmp_path):
    output = tmp_path / "shared.npy"
    start = threading.Barrier(2)
    failures: list[Exception] = []

    # 功能说明：把指定数组写到共享缓存路径，供两个并发任务同时调用以制造竞争。
    def save(value: float) -> None:
        try:
            start.wait(timeout=1)
            atomic_save(output, np.full((32, 32), value, dtype=np.float32))
        except Exception as exc:  # noqa: BLE001
            failures.append(exc)

    first = threading.Thread(target=save, args=(1.0,))
    second = threading.Thread(target=save, args=(2.0,))
    first.start()
    second.start()
    first.join(timeout=2)
    second.join(timeout=2)

    assert not failures
    saved = np.load(output)
    assert saved.shape == (32, 32)
    assert np.all((saved == 1.0) | (saved == 2.0))


# 功能说明：让两个任务并发写同一份元数据 JSON，确认结果始终是可解析的完整对象。
def test_atomic_write_json_allows_two_web_jobs_to_write_the_same_metadata_path(tmp_path):
    output = tmp_path / "shared.npy.json"
    start = threading.Barrier(2)
    failures: list[Exception] = []

    # 功能说明：把给定字典写到共享元数据路径，供并发线程重复调用。
    def write(value: float) -> None:
        try:
            start.wait(timeout=1)
            atomic_write_json(output, {"value": value})
        except Exception as exc:  # noqa: BLE001
            failures.append(exc)

    first = threading.Thread(target=write, args=(1.0,))
    second = threading.Thread(target=write, args=(2.0,))
    first.start()
    second.start()
    first.join(timeout=2)
    second.join(timeout=2)

    assert not failures
    assert json.loads(output.read_text(encoding="utf-8"))["value"] in {1.0, 2.0}
