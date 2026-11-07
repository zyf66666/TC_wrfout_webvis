"""从旧中心定位代码抽出的 Bezier/PCC 纯算法，不依赖 Web 或旧处理目录。"""

from __future__ import annotations
import numpy as np

CENTER_ALGORITHM = "bezier_pcc_v1"


def _clamp(value, low, high):
    return min(max(int(value), low), high)


def pcc_first_guess(data, *, dist, initial_yx=None):
    """生成 PCC 首猜。

    没有外部首猜时沿用旧算法，从全场最低压附近寻找；提供上一层中心时，
    只允许在该中心 ``dist`` 格点以内选择本层最低压，防止跳到远处极值。
    """
    values = np.asarray(data, float)
    if initial_yx is not None:
        initial = np.asarray(initial_yx, dtype=float).ravel()
        if initial.size < 2 or not np.isfinite(initial[:2]).all():
            raise ValueError("中心第一猜测必须是有限的 [y, x]")
        y0 = _clamp(round(initial[0]), 0, values.shape[0] - 1)
        x0 = _clamp(round(initial[1]), 0, values.shape[1] - 1)
        yy, xx = np.ogrid[: values.shape[0], : values.shape[1]]
        allowed = (yy - y0) ** 2 + (xx - x0) ** 2 <= dist * dist
        candidate = np.where(allowed, values, np.inf)
        return tuple(map(int, np.unravel_index(np.argmin(candidate), values.shape)))

    y0, x0 = np.unravel_index(np.argmin(values), values.shape)
    if dist <= 0:
        return int(y0), int(x0)
    yy, xx = np.ogrid[-dist : dist + 1, -dist : dist + 1]
    kernel = (yy * yy + xx * xx <= dist * dist).astype(float)
    try:
        from scipy.signal import fftconvolve

        mean = fftconvolve(values, kernel, mode="same") / fftconvolve(
            np.ones_like(values), kernel, mode="same"
        )
    except ImportError:
        mean = np.full_like(values, np.inf)
        for y in range(values.shape[0]):
            for x in range(values.shape[1]):
                sy = slice(max(0, y - dist), min(values.shape[0], y + dist + 1))
                sx = slice(max(0, x - dist), min(values.shape[1], x + dist + 1))
                my, mx = np.ogrid[sy.start : sy.stop, sx.start : sx.stop]
                include = (my - y) ** 2 + (mx - x) ** 2 <= dist * dist
                mean[y, x] = values[sy, sx][include].mean()
    candidate = np.full_like(values, np.inf)
    ys = slice(max(0, y0 - dist), min(values.shape[0], y0 + dist + 1))
    xs = slice(max(0, x0 - dist), min(values.shape[1], x0 + dist + 1))
    yy, xx = np.ogrid[ys.start : ys.stop, xs.start : xs.stop]
    mask = (yy - y0) ** 2 + (xx - x0) ** 2 <= dist * dist
    candidate[ys, xs][mask] = mean[ys, xs][mask]
    return tuple(map(int, np.unravel_index(np.argmin(candidate), candidate.shape)))


def pcc_basic_value(data, *, border=20):
    ny, nx = np.shape(data)
    w = min(max(int(border), 1), ny // 4, nx // 4)
    if w < 1:
        raise ValueError(f"中心场太小，不能计算边界背景值: {np.shape(data)}")
    return float(
        (
            np.mean(data[:w])
            + np.mean(data[-w:])
            + np.mean(data[:, :w])
            + np.mean(data[:, -w:])
        )
        / 4
    )


def pcc_bezier_interp(data, center_y, center_x, radius):
    values = np.asarray(data, float)
    out = np.zeros((360, radius + 1))
    out[:, 0] = values[center_y, center_x]
    if not radius:
        return out
    angle = np.deg2rad(np.arange(360))[:, None]
    r = np.arange(1, radius + 1)[None, :]
    xp = r * np.cos(angle)
    yp = r * np.sin(angle)
    xf = np.floor(xp)
    yf = np.floor(yp)
    tx = xp - xf
    ty = yp - yf
    x0 = center_x + xf.astype(int)
    x1 = center_x + np.ceil(xp).astype(int)
    x2 = x1 + 1
    xm = x0 - 1
    y0 = center_y + yf.astype(int)
    y1 = center_y + np.ceil(yp).astype(int)
    y2 = y1 + 1
    ym = y0 - 1

    def ix(y):
        return (
            values[y, x0]
            + tx * (values[y, x1] - values[y, x0])
            + 0.25
            * tx
            * (tx - 1)
            * (values[y, x2] - values[y, x1] - values[y, x0] + values[y, xm])
        )

    a, b, c, d = ix(ym), ix(y0), ix(y1), ix(y2)
    out[:, 1:] = b + ty * (c - b) + 0.25 * ty * (ty - 1) * (a + d - b - c)
    return out


def pcc_centroid(polar):
    polar = np.asarray(polar, float)
    radius = polar.shape[1] - 1
    profile = np.concatenate((polar[180:][..., ::-1], polar[:180, 1:]), axis=1)
    positions = np.arange(1, 2 * radius + 2)
    sums = profile.sum(1)
    weighted = (profile * positions).sum(1)
    loc = np.divide(
        weighted, sums, out=np.zeros_like(weighted), where=np.abs(sums) > 1e-12
    )
    offset = np.where(np.abs(sums) > 1e-12, loc - (radius + 1), 0)
    angle = np.deg2rad(np.arange(180))
    return float(np.sum(offset * np.cos(angle)) / 180), float(
        np.sum(offset * np.sin(angle)) / 180
    )


def pcc_iterate(
    data,
    *,
    center_y,
    center_x,
    background,
    cross_radius=121,
    pcc_radius=44,
    max_iterations=10,
    search_center_yx=None,
    search_radius_grid=None,
):
    values = np.asarray(data, float)
    ny, nx = values.shape
    cross = int(cross_radius)
    radius = int(pcc_radius)
    if ny < 2 * cross + 1 or nx < 2 * cross + 1:
        raise ValueError(f"中心场 {values.shape} 小于 PCC 所需 {(2*cross+1,2*cross+1)}")
    cy, cx = _clamp(center_y, cross, ny - cross - 1), _clamp(
        center_x, cross, nx - cross - 1
    )

    def constrain(y, x):
        """把迭代中心限制在上一层中心的搜索圆内。"""
        if search_center_yx is None or search_radius_grid is None:
            return y, x
        origin_y, origin_x = map(float, np.asarray(search_center_yx).ravel()[:2])
        delta_y, delta_x = float(y) - origin_y, float(x) - origin_x
        distance = float(np.hypot(delta_y, delta_x))
        if distance > float(search_radius_grid) and distance > 0:
            scale = float(search_radius_grid) / distance
            y = origin_y + delta_y * scale
            x = origin_x + delta_x * scale
        return _clamp(round(y), cross, ny - cross - 1), _clamp(
            round(x), cross, nx - cross - 1
        )

    cy, cx = constrain(cy, cx)
    for _ in range(max_iterations):
        oy, ox = cy, cx
        patch = (
            values[cy - cross : cy + cross + 1, cx - cross : cx + cross + 1]
            - background
        )
        dx, dy = pcc_centroid(pcc_bezier_interp(patch, cross, cross, radius))
        cy, cx = _clamp(oy + np.round(dy), cross, ny - cross - 1), _clamp(
            ox + np.round(dx), cross, nx - cross - 1
        )
        cy, cx = constrain(cy, cx)
        if (cy, cx) == (oy, ox):
            break
    return cy, cx


def find_pressure_center(
    field,
    *,
    dx_km=1.0,
    dy_km=None,
    initial_yx=None,
    search_radius_km=60.0,
    cross_radius_grid=121,
    pcc_radius_grid=44,
    boundary_width=20,
    max_iterations=10,
):
    """按旧口径执行 firstguess + Bezier PCC，返回取整的 y、x、气压。"""
    del dy_km
    data = np.asarray(field, float)
    if data.ndim != 2 or not np.isfinite(data).all():
        raise ValueError("中心压力场必须为不含缺测值的二维数组")
    dist = int(search_radius_km / float(dx_km))
    y, x = pcc_first_guess(data, dist=dist, initial_yx=initial_yx)
    bg = pcc_basic_value(data, border=boundary_width)
    y, x = pcc_iterate(
        data,
        center_y=y,
        center_x=x,
        background=bg,
        cross_radius=cross_radius_grid,
        pcc_radius=pcc_radius_grid,
        max_iterations=max_iterations,
        search_center_yx=initial_yx,
        search_radius_grid=dist if initial_yx is not None else None,
    )
    return float(y), float(x), float(data[y, x])


# ---------------------------------------------------------------------------
# v2：平滑首猜 + 浮点 PCC（2026-10-13）
#
# v1 的两个问题（见 WRF_315 诊断）：
#   1. 首猜锚定在全场单格点最低气压附近；弱、宽涡旋阶段，螺旋雨带里的窄低压
#      常比涡旋中心更低，首猜被拉到雨带上；
#   2. PCC 迭代按整数格点取整，且质心位移在 180 个方向上平均后只有真实位移的
#      一半，偏移不足 0.5 格就停止，实际上几乎不修正首猜。
# v2：首猜取域内 first_guess_radius_km 圆盘平均气压最低处；随后在浮点坐标上
# 双线性取样做 PCC，增益按 cos² 的平均值 1/2 校正（除以 90），直到位移小于
# tolerance_grid 格。结果基本不依赖起点。
# ---------------------------------------------------------------------------

CENTER_ALGORITHM_V2 = "smooth_fg_float_pcc_v2"


def _disk_mean(values, radius):
    """半径 ``radius`` 格的圆盘平均，边缘按实际覆盖格点数归一。"""
    radius = int(radius)
    yy, xx = np.ogrid[-radius : radius + 1, -radius : radius + 1]
    kernel = (yy * yy + xx * xx <= radius * radius).astype(float)
    try:
        from scipy.signal import fftconvolve
    except ImportError:  # pragma: no cover - scipy 是处理环境的依赖
        fftconvolve = None
    if fftconvolve is not None:
        total = fftconvolve(values, kernel, mode="same")
        count = fftconvolve(np.ones_like(values), kernel, mode="same")
    else:  # pragma: no cover
        shape = [values.shape[i] + kernel.shape[i] - 1 for i in range(2)]
        def conv(a):
            full = np.fft.irfft2(np.fft.rfft2(a, shape) * np.fft.rfft2(kernel, shape), shape)
            return full[radius : radius + values.shape[0], radius : radius + values.shape[1]]
        total, count = conv(values), conv(np.ones_like(values))
    return total / count


def _bilinear(values, y, x):
    y0 = np.floor(y).astype(int)
    x0 = np.floor(x).astype(int)
    ty = y - y0
    tx = x - x0
    return (
        values[y0, x0] * (1 - ty) * (1 - tx)
        + values[y0 + 1, x0] * ty * (1 - tx)
        + values[y0, x0 + 1] * (1 - ty) * tx
        + values[y0 + 1, x0 + 1] * ty * tx
    )


def pcc_float_iterate(
    data,
    *,
    center_y,
    center_x,
    background,
    pcc_radius=80,
    margin=None,
    max_iterations=50,
    tolerance=0.05,
    search_center_yx=None,
    search_radius_grid=None,
):
    """浮点坐标的 PCC 迭代，返回 ``(y, x, iterations, converged)``。

    每次在当前中心周围 ``pcc_radius`` 格内按 1° 方位、1 格半径双线性取样，
    求各直径上气压距平的质心偏移，再按 cos/sin 投影平均（除以 90）得到位移。
    中心被限制在离边界 ``margin`` 格以内；给出搜索圆时同时限制在圆内。
    """
    values = np.asarray(data, float)
    ny, nx = values.shape
    radius = int(pcc_radius)
    margin = radius + 1 if margin is None else max(int(margin), radius + 1)
    if ny <= 2 * margin + 1 or nx <= 2 * margin + 1:
        raise ValueError(f"中心场 {values.shape} 小于 PCC 所需边距 {margin}")

    def constrain(y, x):
        if search_center_yx is not None and search_radius_grid is not None:
            oy, ox = map(float, np.asarray(search_center_yx).ravel()[:2])
            dy, dx = y - oy, x - ox
            distance = float(np.hypot(dy, dx))
            if distance > float(search_radius_grid) and distance > 0:
                scale = float(search_radius_grid) / distance
                y, x = oy + dy * scale, ox + dx * scale
        return min(max(y, margin), ny - 1 - margin), min(max(x, margin), nx - 1 - margin)

    angle = np.deg2rad(np.arange(360))[:, None]
    r = np.arange(0, radius + 1)[None, :]
    sin_a, cos_a = np.sin(angle), np.cos(angle)
    half = np.deg2rad(np.arange(180))
    positions = np.arange(1, 2 * radius + 2)
    y, x = constrain(float(center_y), float(center_x))
    for iteration in range(1, int(max_iterations) + 1):
        polar = _bilinear(values, y + r * sin_a, x + r * cos_a) - background
        profile = np.concatenate((polar[180:][..., ::-1], polar[:180, 1:]), axis=1)
        sums = profile.sum(1)
        weighted = (profile * positions).sum(1)
        valid = np.abs(sums) > 1e-12
        offset = np.where(valid, np.divide(weighted, sums, out=np.zeros_like(weighted), where=valid) - (radius + 1), 0.0)
        dx = float(np.sum(offset * np.cos(half)) / 90.0)
        dy = float(np.sum(offset * np.sin(half)) / 90.0)
        new_y, new_x = constrain(y + dy, x + dx)
        moved = float(np.hypot(new_y - y, new_x - x))
        y, x = new_y, new_x
        if moved < float(tolerance):
            return y, x, iteration, True
    return y, x, int(max_iterations), False


def find_pressure_center_v2(
    field,
    *,
    dx_km=1.0,
    dy_km=None,
    initial_yx=None,
    search_radius_km=30.0,
    first_guess_radius_km=50.0,
    pcc_radius_km=80.0,
    cross_radius_grid=121,
    boundary_width=20,
    max_iterations=50,
    tolerance_grid=0.05,
    **_ignored,
):
    """v2 中心定位，返回 ``(y, x, pressure, info)``，``y``/``x`` 为浮点格点坐标。

    ``initial_yx`` 为空时，首猜取离边界 ``cross_radius_grid`` 格以内
    ``first_guess_radius_km`` 圆盘平均气压的最低点；给出 ``initial_yx`` 时从该点
    出发，并把结果限制在其 ``search_radius_km`` 以内。``pressure`` 为取整格点上的值。
    """
    del dy_km, _ignored
    data = np.asarray(field, float)
    if data.ndim != 2 or not np.isfinite(data).all():
        raise ValueError("中心压力场必须为不含缺测值的二维数组")
    dx = float(dx_km)
    margin = int(cross_radius_grid)
    pcc_radius = max(1, int(round(float(pcc_radius_km) / dx)))
    background = pcc_basic_value(data, border=boundary_width)
    if initial_yx is None:
        smooth = _disk_mean(data, max(1, int(round(float(first_guess_radius_km) / dx))))
        candidate = np.full_like(smooth, np.inf)
        ny, nx = data.shape
        if ny <= 2 * margin + 1 or nx <= 2 * margin + 1:
            raise ValueError(f"中心场 {data.shape} 小于首猜所需边距 {margin}")
        candidate[margin : ny - margin, margin : nx - margin] = smooth[margin : ny - margin, margin : nx - margin]
        first_guess = tuple(float(v) for v in np.unravel_index(np.argmin(candidate), candidate.shape))
        search_center, search_radius = None, None
    else:
        initial = np.asarray(initial_yx, dtype=float).ravel()
        if initial.size < 2 or not np.isfinite(initial[:2]).all():
            raise ValueError("中心第一猜测必须是有限的 [y, x]")
        first_guess = (float(initial[0]), float(initial[1]))
        search_center, search_radius = first_guess, float(search_radius_km) / dx
    y, x, iterations, converged = pcc_float_iterate(
        data,
        center_y=first_guess[0],
        center_x=first_guess[1],
        background=background,
        pcc_radius=pcc_radius,
        margin=margin,
        max_iterations=max_iterations,
        tolerance=tolerance_grid,
        search_center_yx=search_center,
        search_radius_grid=search_radius,
    )
    iy = min(max(int(round(y)), 0), data.shape[0] - 1)
    ix = min(max(int(round(x)), 0), data.shape[1] - 1)
    info = {
        "center_yx_float": [round(y, 3), round(x, 3)],
        "first_guess_yx": [round(first_guess[0], 3), round(first_guess[1], 3)],
        "iterations": int(iterations),
        "converged": bool(converged),
    }
    return y, x, float(data[iy, ix]), info
