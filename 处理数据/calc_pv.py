# 文件用途：这是一次性位涡检查脚本：读取 d03 wrfout 的 U、V、温度和气压，使用 d03 或 d01 的科氏参数计算位涡，并在终端打印数组形状及最小、最大、平均值。
from netCDF4 import Dataset
from wrf import pvo
import numpy as np


# ============================================================
# 文件路径
# ============================================================

wrfout_path = (
    "/Volumes/land_1/WRF_315_land/run/"
    "wrfout_d03_2023-09-01_000000"
)

wrfinput_d01_path = (
    "/Volumes/land_1/WRF_315_land/run/"
    "wrfinput_d01"
)

timeidx = 0


# ============================================================
# 1. 打开 d03 wrfout
# ============================================================

nc = Dataset(wrfout_path)


# ============================================================
# 2. 读取计算 PV 所需变量
# ============================================================

# U:
# (bottom_top, south_north, west_east_stag)
u = np.asarray(
    nc.variables["U"][timeidx]
)

# V:
# (bottom_top, south_north_stag, west_east)
v = np.asarray(
    nc.variables["V"][timeidx]
)

# WRF T 是 perturbation potential temperature
# theta = T + 300 K
theta = (
    np.asarray(nc.variables["T"][timeidx])
    + 300.0
)

# 全气压 P + PB，单位 Pa
pressure = (
    np.asarray(nc.variables["P"][timeidx])
    +
    np.asarray(nc.variables["PB"][timeidx])
)


# ============================================================
# 3. 网格间距
# ============================================================

dx = float(nc.DX)
dy = float(nc.DY)

print("dx =", dx, "m")
print("dy =", dy, "m")


# ============================================================
# 4. 理想试验 map factor = 1
# ============================================================

# U 网格
mapfac_u = np.ones(
    u.shape[-2:],
    dtype=np.float64
)

# V 网格
mapfac_v = np.ones(
    v.shape[-2:],
    dtype=np.float64
)

# mass grid
mapfac_m = np.ones(
    theta.shape[-2:],
    dtype=np.float64
)


# ============================================================
# 5. Coriolis 参数
# ============================================================

# 优先检查 d03 wrfout 自己有没有 F
if "F" in nc.variables:

    cor = np.asarray(
        nc.variables["F"][timeidx]
    )

    print(
        "Use F from d03 wrfout:",
        np.nanmin(cor),
        np.nanmax(cor)
    )

else:

    # 如果 d03 没有 F，
    # 从 wrfinput_d01 获取 f-plane 的常数 f
    nc01 = Dataset(wrfinput_d01_path)

    f_d01 = np.asarray(
        nc01.variables["F"][0]
    )

    print(
        "d01 F range:",
        np.nanmin(f_d01),
        np.nanmax(f_d01)
    )

    # f-plane 下所有格点应该基本相同
    f0 = float(np.nanmean(f_d01))

    print("f0 =", f0, "s^-1")

    # 构造 d03 大小的 Coriolis 数组
    cor = np.full(
        theta.shape[-2:],
        f0,
        dtype=np.float64
    )

    nc01.close()


# ============================================================
# 6. 查看维度，确认正确
# ============================================================

print()
print("U shape       :", u.shape)
print("V shape       :", v.shape)
print("theta shape   :", theta.shape)
print("pressure shape:", pressure.shape)

print("mapfac_u shape:", mapfac_u.shape)
print("mapfac_v shape:", mapfac_v.shape)
print("mapfac_m shape:", mapfac_m.shape)
print("cor shape     :", cor.shape)


# ============================================================
# 7. 计算 Potential Vorticity
# ============================================================

pv = pvo(
    u,
    v,
    theta,
    pressure,
    mapfac_u,
    mapfac_v,
    mapfac_m,
    cor,
    dx,
    dy,
    meta=False
)


# ============================================================
# 8. 输出结果
# ============================================================

print()
print("PV shape:", pv.shape)
print("PV min :", np.nanmin(pv), "PVU")
print("PV max :", np.nanmax(pv), "PVU")
print("PV mean:", np.nanmean(pv), "PVU")


nc.close()
