"""Standalone worker, executed by the selected Conda environment's Python."""
from __future__ import annotations

import json
from pathlib import Path
import runpy
import sys


def main() -> None:
    source = Path(sys.argv[1])
    output = Path(sys.argv[2])
    dpi = int(sys.argv[3])
    check_only = len(sys.argv) > 4 and sys.argv[4] == "--check"
    # Compile separately so checking never imports or executes the user's code.
    compile(source.read_text(encoding="utf-8"), str(source), "exec")
    if check_only:
        print("Python 语法检查通过", flush=True)
        return

    import matplotlib
    matplotlib.use("Agg", force=True)
    import matplotlib.pyplot as plt

    plt.rcParams["savefig.dpi"] = dpi
    plt.show = lambda *args, **kwargs: None
    # Preserve project-local imports when the runner itself is outside the repo.
    sys.path.insert(0, str(Path.cwd()))
    sys.argv = [str(source)]
    try:
        runpy.run_path(str(source), run_name="__main__", init_globals={"OUTPUT_PATH": str(output), "DPI": dpi})
        if not output.exists():
            figures = plt.get_fignums()
            if not figures:
                raise RuntimeError("没有生成图片：请创建一张 Matplotlib 图，或将 PNG 保存到 OUTPUT_PATH")
            if len(figures) != 1:
                raise RuntimeError("生成了多张 Matplotlib 图：请将所需的一张图保存到 OUTPUT_PATH")
            plt.figure(figures[0]).savefig(output, format="png", dpi=dpi)
            axes = _describe_axes(plt.figure(figures[0]), dpi)
        else:
            axes = None
        runtime = {"python_executable": sys.executable, "python_prefix": sys.prefix}
        if axes:
            runtime["axes"] = axes
        output.with_suffix(".json").write_text(json.dumps(runtime), encoding="utf-8")
    finally:
        plt.close("all")


def _describe_axes(fig, dpi: int):
    """记录各子图位置（供看板批注按物理坐标定位）；失败时返回 None。"""
    try:
        import importlib.util

        path = Path(__file__).resolve().parents[1] / "plotting" / "figure_axes.py"
        spec = importlib.util.spec_from_file_location("wrf_figure_axes", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module.describe_axes(fig, {"dpi": dpi})
    except Exception:  # noqa: BLE001
        return None


if __name__ == "__main__":
    main()
