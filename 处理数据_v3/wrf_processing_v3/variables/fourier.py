from ..common import asymmetric, extract_wave
from ._helpers import save


def process(ctx):
    # 导入 / 读取极坐标 source（方位为第 0 维）/ 计算保存
    spec = ctx.spec.options
    values = ctx.read(spec["source"])
    source_metadata = getattr(ctx, "read_metadata", lambda name: {})(spec["source"])
    geometry = {
        key: source_metadata[key]
        for key in (
            "radius_km",
            "radius_resolution_km",
            "source_shape",
            "center_mode",
            "angle_count",
        )
        if key in source_metadata
    }
    mode = spec.get("mode", "asymmetry")
    if mode == "asymmetry":
        out = asymmetric(values)
    elif mode == "wave":
        out = extract_wave(values, int(spec["wavenumber"]), coefficient_cache=getattr(ctx,"cached_calculation",None))
    else:
        raise ValueError(f"不支持 Fourier mode: {mode}")
    save(
        ctx,
        out,
        units=spec.get("units", ""),
        source=spec["source"],
        mode=mode,
        wavenumber=spec.get("wavenumber"),
        azimuth_axis=0,
        **geometry,
    )
