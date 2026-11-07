"""Advisory process locks for NetCDF variable files."""

from __future__ import annotations

from contextlib import contextmanager
import fcntl
from pathlib import Path
import time
from typing import Iterator


class LockTimeoutError(TimeoutError):
    pass


@contextmanager
def file_lock(
    data_path: Path,
    *,
    exclusive: bool,
    timeout_seconds: float = 120.0,
) -> Iterator[Path]:
    lock_path = Path(f"{data_path}.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    operation = fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH
    deadline = time.monotonic() + float(timeout_seconds)
    with lock_path.open("a+b") as handle:
        while True:
            try:
                fcntl.flock(handle.fileno(), operation | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    mode = "写" if exclusive else "读"
                    raise LockTimeoutError(
                        f"等待 {data_path} 的{mode}锁超时（{timeout_seconds:g} s）"
                    )
                time.sleep(0.05)
        try:
            yield lock_path
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
