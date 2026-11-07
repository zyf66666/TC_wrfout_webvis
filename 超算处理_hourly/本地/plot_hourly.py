#!/usr/bin/env python3
"""本地画逐小时强度与路径：轴平均最大风速、局地最大风速、最低海平面气压、中心路径（2×2）。

输入可以是（可混用、可多个）：
  * 超算下载包 tc_hourly_<试验>.tar.gz（不用解压）
  * 解压后的 <试验>/ 目录或其中的 <试验>_hourly.csv
  * 本地 processed_v3 / post 里的试验目录（含 d03/*.nc），例如对照组：
        /Volumes/land_1/processed_v3/WRF_0_sea
    （读 axis_mean_max_wind、local_max_wind、slp_center、center_geo、slp 的整点时次；需要 netCDF4）

用法（wrf-webvis 环境）：
  conda activate wrf-webvis
  python plot_hourly.py ~/Downloads/tc_hourly_WRF_0_sea_P1.tar.gz
  python plot_hourly.py tc_hourly_WRF_0_sea_P1.tar.gz --control /Volumes/land_1/processed_v3/WRF_0_sea
  python plot_hourly.py a.tar.gz b.tar.gz --hours 0 96 --out 强度对比.png
"""

from __future__ import annotations

import argparse
import io
from pathlib import Path
import sys
import tarfile

import numpy as np

# 与超算端共用同一份读表代码（同级文件夹 超算端/v3_table.py）
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "超算端"))
from v3_table import build_table, column, read_csv  # noqa: E402

# 分类色（固定顺序，不循环）；对照组用深灰
SERIES_COLORS = ("#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300")
CONTROL_COLOR = "#3d3d3a"
INK, MUTED, GRID = "#1f1f1e", "#6b6a64", "#e4e3de"


def _csv_from_tar(path: Path):
    with tarfile.open(path, "r:gz") as archive:
        members = [m for m in archive.getmembers() if m.isfile() and m.name.endswith("_hourly.csv")]
        if not members:
            raise ValueError(f"{path} 里没有 *_hourly.csv")
        member = members[0]
        text = archive.extractfile(member).read().decode("utf-8")
    rows = read_csv(io.StringIO(text))
    name = Path(member.name).name[: -len("_hourly.csv")]
    return name, rows


def load(path_text: str, step: int):
    """返回 (名字, 行列表)。"""
    path = Path(path_text).expanduser()
    if not path.exists():
        raise FileNotFoundError(path)
    if path.is_file() and path.name.endswith((".tar.gz", ".tgz")):
        return _csv_from_tar(path)
    if path.is_file() and path.suffix == ".csv":
        return path.name.replace("_hourly.csv", ""), read_csv(path)
    if path.is_dir():
        csvs = sorted(path.glob("*_hourly.csv"))
        if csvs:
            return path.name, read_csv(csvs[0])
        domain_dir = path if (path / "axis_mean_max_wind.nc").exists() else path / "d03"
        if (domain_dir / "axis_mean_max_wind.nc").exists() or (domain_dir / "slp_center.nc").exists():
            name = path.name if domain_dir != path else path.parent.name
            print(f"读取 v3 目录 {domain_dir}（整点时次，含 slp 全场最小值，外接盘上可能要一两分钟）…", flush=True)
            return name, build_table(domain_dir, step_minutes=step)
    raise ValueError(f"认不出 {path}：需要 .tar.gz、*_hourly.csv、或含 d03/*.nc 的试验目录")


def _setup_fonts():
    import matplotlib
    from matplotlib import font_manager

    wanted = ["PingFang SC", "Heiti SC", "Hiragino Sans GB", "Arial Unicode MS",
              "Noto Sans CJK SC", "Noto Sans CJK TC", "Source Han Sans SC", "SimHei", "Microsoft YaHei"]
    have = {font.name for font in font_manager.fontManager.ttflist}
    chosen = [name for name in wanted if name in have]
    matplotlib.rcParams["font.sans-serif"] = chosen + ["DejaVu Sans"]
    matplotlib.rcParams["axes.unicode_minus"] = False


def _style_axis(ax, title, ylabel=None, xlabel=None):
    ax.set_title(title, loc="left", fontsize=11.5, color=INK, pad=8)
    if ylabel:
        ax.set_ylabel(ylabel, color=MUTED, fontsize=10)
    if xlabel:
        ax.set_xlabel(xlabel, color=MUTED, fontsize=10)
    ax.grid(True, color=GRID, linewidth=0.7)
    ax.set_axisbelow(True)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color("#b9b8b1")
    ax.tick_params(colors=MUTED, labelsize=9)


def plot(series, out: Path, hours=None, title=None, dpi=200):
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    _setup_fonts()
    fig, axes = plt.subplots(2, 2, figsize=(14, 9), constrained_layout=True)
    (ax_axis, ax_local), (ax_slp, ax_track) = axes
    panels = (
        (ax_axis, "vmax_axis_mean_ms", "(a) 轴平均最大风速（最低模式层）", "m s$^{-1}$"),
        (ax_local, "vmax_local_ms", "(b) 局地最大风速（最低模式层）", "m s$^{-1}$"),
        (ax_slp, "slp_min_hPa", "(c) 最低海平面气压（d03 全场最小）", "hPa"),
    )
    for name, rows, color, is_control in series:
        hours_axis = column(rows, "time_h")
        keep = np.ones_like(hours_axis, dtype=bool)
        if hours:
            keep = (hours_axis >= hours[0]) & (hours_axis <= hours[1])
        width = 1.6 if not is_control else 1.4
        style = "-" if not is_control else (0, (5, 2.5))
        for ax, key, _, _ in panels:
            values = column(rows, key)
            if key == "slp_min_hPa" and not np.isfinite(values[keep]).any():
                values = column(rows, "slp_center_5km_hPa")  # 没有 slp.nc 时退回中心 5 km 平均
            ax.plot(hours_axis[keep], values[keep], linestyle=style, color=color, linewidth=width, label=name,
                    solid_capstyle="round")
        lat, lon = column(rows, "center_lat")[keep], column(rows, "center_lon")[keep]
        good = np.isfinite(lat) & np.isfinite(lon)
        if good.any():
            ax_track.plot(lon[good], lat[good], linestyle=style, color=color, linewidth=width, label=name)
            hh = hours_axis[keep][good]
            marks = np.isclose(np.mod(hh, 12), 0)
            ax_track.plot(lon[good][marks], lat[good][marks], "o", color=color, markersize=4.5,
                          markeredgecolor="white", markeredgewidth=0.8)
            ax_track.plot([lon[good][0]], [lat[good][0]], "o", color=color, markersize=7,
                          markeredgecolor="white", markeredgewidth=1.2)
            ax_track.annotate(f"{hh[-1]:g} h", (lon[good][-1], lat[good][-1]), xytext=(5, 3),
                              textcoords="offset points", fontsize=8.5, color=MUTED)

    for ax, _, label, unit in panels:
        _style_axis(ax, label, unit, "积分时间 (h)")
        if hours:
            ax.set_xlim(*hours)
    _style_axis(ax_track, "(d) 中心路径（slp_center，圆点每 12 h，大圆点为起点）", "纬度 (°N)", "经度 (°E)")
    lats = np.concatenate([column(rows, "center_lat") for _, rows, _, _ in series])
    if np.isfinite(lats).any():
        ax_track.set_aspect(1.0 / np.cos(np.deg2rad(np.nanmean(lats))), adjustable="datalim")

    if len(series) >= 2:
        handles, labels = ax_axis.get_legend_handles_labels()
        fig.legend(handles, labels, loc="outside lower center", ncol=min(len(labels), 6), frameon=False,
                   fontsize=10)
    if title:
        fig.suptitle(title, fontsize=13, color=INK)
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=dpi)
    plt.close(fig)
    return out


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("inputs", nargs="+", help=".tar.gz / *_hourly.csv / 试验目录")
    parser.add_argument("--control", action="append", default=[], help="对照组（虚线深灰），可重复，例如 /Volumes/land_1/processed_v3/WRF_0_sea")
    parser.add_argument("--label", action="append", default=None, help="按 inputs 顺序改图例名，可重复")
    parser.add_argument("--hours", nargs=2, type=float, metavar=("起", "止"), help="只画这段积分时间 (h)")
    parser.add_argument("--step", type=int, default=60, help="读 v3 目录时的时间步长（分钟，默认 60）")
    parser.add_argument("--title", default=None)
    parser.add_argument("--out", type=Path, default=None, help="输出 PNG（默认 当前目录/tc_hourly_<名字>.png）")
    parser.add_argument("--csv", action="store_true", help="同时把每组数据另存成 CSV（v3 目录读出来的对照组常用）")
    args = parser.parse_args(argv)

    series = []
    for i, path in enumerate(args.control):
        name, rows = load(path, args.step)
        series.append((f"{name}（对照）", rows, CONTROL_COLOR if i == 0 else "#8c8b85", True))
    for i, path in enumerate(args.inputs):
        name, rows = load(path, args.step)
        if args.label and i < len(args.label):
            name = args.label[i]
        series.append((name, rows, SERIES_COLORS[i] if i < len(SERIES_COLORS) else None, False))
        if i >= len(SERIES_COLORS):
            raise SystemExit("一张图最多 6 组试验，多了请分几张图画")

    for name, rows, _, _ in series:
        axis, slp = column(rows, "vmax_axis_mean_ms"), column(rows, "slp_min_hPa")
        hours = column(rows, "time_h")
        msg = f"{name}: {len(rows)} 个时次 {np.nanmin(hours):g}–{np.nanmax(hours):g} h" if len(rows) else f"{name}: 无数据"
        if np.isfinite(axis).any():
            k = int(np.nanargmax(axis))
            msg += f"；轴平均最大风峰值 {axis[k]:.1f} m/s @ {hours[k]:g} h"
        if np.isfinite(slp).any():
            k = int(np.nanargmin(slp))
            msg += f"；最低气压 {slp[k]:.1f} hPa @ {hours[k]:g} h"
        print(msg)
        if args.csv:
            from v3_table import write_csv

            safe = name.replace("（对照）", "_control").replace("/", "_")
            print("  CSV:", write_csv(rows, Path(f"{safe}_hourly.csv")).resolve())

    out = args.out or Path(f"tc_hourly_{'_'.join(s[0].replace('（对照）', '') for s in series)[:80]}.png")
    print("图：", plot(series, out, hours=args.hours, title=args.title).resolve())
    return 0


if __name__ == "__main__":
    sys.exit(main())
