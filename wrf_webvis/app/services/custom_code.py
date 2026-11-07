"""Execute trusted plotting scripts in Conda, with parent-owned cancellation."""
from __future__ import annotations

import codecs
import json
import math
import os
from pathlib import Path
import selectors
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import uuid

from PIL import Image

from ..core.config import REPOSITORY_ROOT
from ..core.schemas import PlotRequest
from .progress import ProgressCallback, check_cancellation, emit


LOG_LIMIT = 65536
ERROR_TAIL_LIMIT = 16384
WORKER_PATH = Path(__file__).with_name("custom_code_worker.py")


def conda_executable() -> str:
    configured = os.environ.get("WRF_WEBVIS_CONDA_EXE") or os.environ.get("CONDA_EXE")
    if configured:
        candidates = [configured]
    else:
        candidates = [shutil.which("conda")]
        # LaunchAgent often has a minimal PATH even though its Python is in Conda.
        prefix = Path(sys.prefix)
        if (prefix / "conda-meta").is_dir():
            base = prefix.parent.parent if prefix.parent.name == "envs" else prefix
            candidates.append(str(base / "bin" / "conda"))
    for candidate in candidates:
        if candidate:
            executable = shutil.which(os.path.expanduser(candidate))
            if executable:
                return executable
    raise RuntimeError("找不到 Conda 可执行文件，请配置 WRF_WEBVIS_CONDA_EXE")


def runtime_settings() -> tuple[str, float]:
    environment = os.environ.get("WRF_WEBVIS_CUSTOM_CONDA_ENV", "wrf-webvis").strip()
    try:
        timeout = float(os.environ.get("WRF_WEBVIS_CUSTOM_TIMEOUT_SECONDS", "600"))
    except ValueError as exc:
        raise RuntimeError("WRF_WEBVIS_CUSTOM_TIMEOUT_SECONDS 必须是正数") from exc
    if not environment or not math.isfinite(timeout) or timeout <= 0:
        raise RuntimeError("自定义代码的 Conda 环境不能为空，执行时限必须是有限正数")
    return environment, timeout


def _stop_process_group(process: subprocess.Popen) -> None:
    """Kill the whole group, even if the Conda leader exited before its children."""
    if os.name == "posix":
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            process.wait(timeout=.5)
        except subprocess.TimeoutExpired:
            pass
        # A child can ignore SIGTERM after its parent has already exited.
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    elif process.poll() is None:  # pragma: no cover - macOS/Linux deployment
        subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], capture_output=True)
    process.wait(timeout=2)


def _run(command: list[str], *, cwd: Path, environment: dict[str, str], timeout: float,
         progress: ProgressCallback | None) -> None:
    check_cancellation(progress)
    process = subprocess.Popen(command, cwd=cwd, env=environment, stdin=subprocess.DEVNULL,
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT, start_new_session=True)
    started = time.monotonic()
    tail = ""
    reported = 0
    truncated = False
    decoder = codecs.getincrementaldecoder("utf-8")("replace")

    def record(chunk: bytes, *, final: bool = False) -> None:
        nonlocal tail, reported, truncated
        message = decoder.decode(chunk, final=final)
        tail = (tail + message)[-ERROR_TAIL_LIMIT:]
        if reported < LOG_LIMIT and message:
            visible = message[:LOG_LIMIT - reported]
            reported += len(visible)
            emit(progress, visible)
        if reported >= LOG_LIMIT and not truncated:
            truncated = True
            emit(progress, "脚本日志已达到长度上限，后续输出省略；失败时仍保留末尾错误。")

    try:
        assert process.stdout is not None
        os.set_blocking(process.stdout.fileno(), False)
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            while True:
                check_cancellation(progress)
                if time.monotonic() - started >= timeout:
                    raise RuntimeError(f"自定义代码运行超时（{timeout:g} 秒）")
                for key, _ in selector.select(timeout=.1):
                    chunk = os.read(key.fd, 4096)
                    if chunk:
                        record(chunk)
                    else:
                        selector.unregister(key.fileobj)
                if process.poll() is not None:
                    # Drain buffered output without waiting for inherited pipes.
                    while True:
                        try:
                            chunk = os.read(process.stdout.fileno(), 4096)
                        except BlockingIOError:
                            break
                        if not chunk:
                            break
                        record(chunk)
                    record(b"", final=True)
                    if process.returncode:
                        raise RuntimeError(f"自定义代码执行失败（退出码 {process.returncode}）\n{tail}")
                    return
    finally:
        _stop_process_group(process)
        if process.stdout is not None:
            process.stdout.close()


def run_custom_code(request: PlotRequest, figure_root: Path, request_hash: str,
                    progress: ProgressCallback | None = None, *, check_only: bool = False) -> dict:
    environment_name, timeout = runtime_settings()
    executable = conda_executable()
    with tempfile.TemporaryDirectory(prefix="wrf-custom-") as temporary:
        run_dir = Path(temporary)
        source = run_dir / "custom_code.py"
        output = run_dir / "output.png"
        source.write_text(request.custom_code or "", encoding="utf-8")
        command = [executable, "run", "--no-capture-output", "-n", environment_name, "python", "-u",
                   str(WORKER_PATH), str(source), str(output), str(request.dpi)]
        if check_only:
            command.append("--check")
        child_environment = os.environ.copy()
        child_environment["MPLBACKEND"] = "Agg"
        child_environment.setdefault("MPLCONFIGDIR", str(REPOSITORY_ROOT / ".runtime" / "matplotlib"))
        emit(progress, f"{'检查代码' if check_only else '运行自定义代码'}：conda: {environment_name}")
        _run(command, cwd=REPOSITORY_ROOT, environment=child_environment, timeout=timeout, progress=progress)
        check_cancellation(progress)
        if check_only:
            return {"dry_run": True, "code_checked": True, "plot_id": request_hash,
                    "environment": f"conda: {environment_name}",
                    "plan": {"required_files": [], "missing_files": [], "warnings": []}}
        try:
            with Image.open(output) as image:
                if image.format != "PNG":
                    raise ValueError("output is not PNG")
                image.verify()
            with Image.open(output) as image:
                image.load()
        except (OSError, ValueError, Image.DecompressionBombError) as exc:
            raise RuntimeError("OUTPUT_PATH 必须包含一张有效的 PNG 图片") from exc
        runtime = json.loads(output.with_suffix(".json").read_text(encoding="utf-8"))
        axes = runtime.pop("axes", None)
        figure_root = Path(figure_root)
        figure_root.mkdir(parents=True, exist_ok=True)
        plot_id = f"{request_hash}_{uuid.uuid4().hex[:12]}"
        image_path = figure_root / f"{plot_id}.png"
        # Stage on the destination filesystem before the atomic publication.
        staged = figure_root / f".{plot_id}.tmp"
        try:
            shutil.copyfile(output, staged)
            check_cancellation(progress)
            staged.replace(image_path)
        finally:
            staged.unlink(missing_ok=True)
        result = {"plot_id": plot_id, "image_path": str(image_path),
                  "static_path": f"/static/figures/{plot_id}.png",
                  "metadata": {"title": request.title, "request_hash": request_hash,
                               "conda_env": environment_name, **runtime},
                  "used_files": [], "processed_files": [], "warnings": []}
        if axes:
            result["axes"] = axes
            try:
                from ..plotting.figure_axes import sidecar_path
                sidecar_path(image_path).write_text(json.dumps(axes), encoding="utf-8")
            except OSError:
                pass
        return result
