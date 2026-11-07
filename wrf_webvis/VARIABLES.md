# WRF WebVis 变量说明

本文件对应 `app/core/registry.py` 中当前注册的变量。默认缓存根目录是
`processed/<member>/d0<domain>/`：三维变量保存为
`<variable>/<time>h/<height>m.npy`，二维变量保存为 `<variable>/<time>h.npy`。
派生变量的同名 `.npy.json` 记录依赖、公式、单位和处理参数。

## 坐标、中心与风的约定

- x 向东、y 向北；极坐标方位角 0° 指东、逆时针为正。
- `slp_center` 是每时次海平面气压中心；`layer_center` 是指定几何高度的 pressure 最小值中心。
- 当前缓存中的 `ua/va` 已减去中心轨迹给出的平移速度，是风暴相对东、北风分量；不得再做一次平移订正。
- `U10/V10` 仍是相对于地面的原始10 m风；`UST` 是摩擦速度，不是应力本身。
- `vr/vt` 从缓存 `ua/va` 投影得到，因而也是风暴相对风。`V_r < 0` 表示入流。

## 直接读取的二维、三维场

|变量|维度、单位|来源或计算|表示的物理量|
|---|---|---|---|
|`slp`|2D, hPa|`wrf.getvar("slp")`|海平面气压；用于低层涡旋中心。|
|`cape`|2D, J kg⁻¹|`wrf.getvar("cape_2d")[0]`|最大对流有效位能（MCAPE）；只保存 `cape_2d` 的第一个分量。|
|`mdbz`|2D, dBZ|`wrf.getvar("mdbz")`|原始模式整根气柱的最大模拟反射率；不先插值到配置高度层。支持 `mdbz_polar`，保存为 `mdbz/<time>h.npy`。|
|`dbz`|3D, dBZ|`wrf.getvar("dbz")` 后插值至几何高度|模拟雷达反射率。|
|`ua`, `va`|3D, m s⁻¹|`wrf.getvar("ua"/"va")`、高度插值、减台风平移速度|风暴相对东、北风分量。|
|`u`, `v`|3D, m s⁻¹|`wrf.getvar("u"/"v")` 后高度插值|WRF返回的原始风分量；台风相对分析优先用 `ua/va`。|
|`wa`|3D, m s⁻¹|`wrf.getvar("wa")` 后高度插值|垂直速度，正值向上。|
|`rh`|3D, %|`wrf.getvar("rh")` 后高度插值|相对湿度。|
|`pressure`|3D, hPa|`wrf.getvar("pressure")` 后高度插值|气压；用于密度、中心、切变诊断。|
|`temp`|3D, K|`wrf.getvar("temp")` 后高度插值|气温。|
|`theta_e`|3D, K|`wrf.getvar("theta_e")` 后高度插值|相当位温。|
|`theta_e_anomaly`|3D, K|d03 theta_e 减去同时刻同高度 d02 各层中心 400–500 km 环带的半径加权平均|笛卡尔相当位温异常；`theta_e_anomaly1_polar` 为目标域各层中心极坐标版本。|
|`pvo`|3D, PVU|原始交错网格 `U/V`、`T+300` 和 `P+PB`（Pa）调用 `wrf.pvo`，再高度插值|理想试验位涡；地图因子固定为 1，优先使用当前 `F`，缺失时回退到同试验 `wrfinput_d01` 的常数 `F`。支持 `pvo_polar`，绘图缩放系数为 1。|
|`QVAPOR`|3D, kg kg⁻¹|WRF水汽混合比、后高度插值|水汽混合比；用于水汽通量和密度。|
|`RAINNC`|2D, mm|WRF原变量|累计网格尺度（非参数化对流）降水；时段雨量须作相邻时次差分。|
|`RAINNCV`|2D, mm|WRF原变量|一个输出时间步的非对流降水量；不是天然的 mm h⁻¹，需除以输出间隔换算雨强。|
|`HFX`|2D, W m⁻²|WRF原变量|向上的地表感热通量。|
|`LH`|2D, W m⁻²|WRF原变量|向上的地表潜热通量。|
|`U10`, `V10`|2D, m s⁻¹|WRF原变量|相对于地面的10 m东、北风。|
|`UST`|2D, m s⁻¹|WRF原变量 `UST`|相似理论摩擦速度 u*；应力大小可近似为 rho times u* squared。|
|`PBLH`|2D, m|WRF原变量 `PBLH`|边界层高度。|

已用 `/Volumes/sea_2/WRF_180_sea/run/wrfout_d03_2023-09-01_000000` 核对：`UST` 和 `PBLH` 都存在，原始单位分别为 m s⁻¹、m。

## 跨域相当位温异常

在 `处理数据/process_data.py` 中设置 `DOMAINS = [3]`、
`VARIABLES = ["theta_e_anomaly", "theta_e_anomaly1_polar"]`，直接运行原入口即可。
单独填写 `theta_e_anomaly1_polar` 也会计算其笛卡尔异常源场。

- 环境源：同试验、同时刻、同高度的 `d02/theta_e1_polar`（各层中心）。
- 环境范围：`OPTIONS["theta_e_environment_ring_km"] = [400, 500]`，含端点、全方位。
- 环境域：`OPTIONS["theta_e_environment_domain"] = 2`；与 `DOMAINS` 的目标域独立。
- 径向坐标：读取环境源元数据中的 `radius_resolution_km`，d02 通常为 5 km。
- 环境平均：`sum(r * theta_e) / sum(r)`，只统计有限样本，每个时刻和高度单独计算。
- 异常：目标域 `theta_e - 环境平均`，保持原笛卡尔网格，不需要跨域网格重采样。
- 原始 theta_e 和 d02 环境数据只读；缺失时报错，不自动重算环境源。

输出为 `d03/theta_e_anomaly/<time>h/<height>m.npy` 和
`d03/theta_e_anomaly1_polar/<time>h/<height>m.npy`，单位 K。
笛卡尔输出旁的 `*.npy.environment.json` 记录该时刻高度的环境值、有效覆盖率、
环带定义和输入文件签名。环境场、目标源场或参数变化时会重新计算异常。

极坐标输出半径由原有 `polar_max_radius_km` 控制，当前为 300 km；
环境环带 400–500 km 不要求 d03 也覆盖到 500 km。
继续沿用目标域各层中心及原有边界审计；不安全时次按
`LAYER_POLAR_BOUNDARY_POLICY` 筛选，并记录在对应的 safety 报告中。
缺少目标域中心时，沿用原处理流程准备 pressure 和各层中心。

网页绘制水平图用 `theta_e_anomaly`，绘制各层中心 radius-height 图用
`theta_e_anomaly1`（网页不填 `_polar` 后缀）；建议色图 `RdBu_r`，色标单位 K。

radius-height 图可在“半径-高度图”设置中打开“绘制 RMW”。
普通低层中心极坐标场选 `rmw_by_height`，各层中心极坐标场选
`rmw_by_layer_center`；两者均需预先处理。单时次显示各高度的 RMW，
多时次则对每个高度的有效值求时间平均，缺测高度保留断点。
“RMW 高度平滑 σ (km)”可对图上的曲线按实际高度间距作高斯平滑；
默认 0 不平滑，平滑不会修改原始 RMW 数据，也不会跨过缺测高度。

## 风场与新增边界层派生场

|变量|维度、单位|计算方法|表示的物理量|
|---|---|---|---|
|`wind_speed_10`|2D, m s⁻¹|sqrt(U10 squared + V10 squared)|10 m风速大小。|
|`vr`|3D, m s⁻¹|u_sr cos(theta) + v_sr sin(theta)|风暴相对径向风；负值为入流。|
|`vt`|3D, m s⁻¹|-u_sr sin(theta) + v_sr cos(theta)|风暴相对切向风。|
|`convergence`|3D, s⁻¹|-(du_sr/dx + dv_sr/dy)|水平速度辐合；正值代表辐合。|
|`mass_convergence`|3D, kg m⁻³ s⁻¹|-div(rho times V_sr)|质量通量辐合。rho = p / [Rd T (1 + 0.61 qv)]；pressure 缓存为 hPa，程序内部转 Pa。|
|`mfc`|3D, kg m⁻³ s⁻¹|-div(rho times qv times V_sr)|水汽通量辐合；正值表示水汽汇聚。|
|`qflux_r`|3D, kg m⁻² s⁻¹|rho times qv times Vr_sr|径向水汽通量；负值表示向中心的水汽输送。|
|`inflow_angle`|3D, degree|atan2(-Vr_sr, Vt_sr)|入流角；在气旋式切向风背景下，较大的正值表示更强的跨切向入流。中心点无物理意义。|
|`bl_mass_convergence`|2D, kg m⁻² s⁻¹|从 z1 到 z2 积分 mass_convergence dz|边界层质量通量辐合。默认100–1000 m。|
|`bl_mfc`|2D, kg m⁻² s⁻¹|从 z1 到 z2 积分 mfc dz|边界层水汽通量辐合；适合直接与LS降水比较。|

散度/辐合未调用 wrf-python 内置诊断：当前 wrf-python 1.4.2 没有公开的水平散度函数，`getvar("div")` 也不是有效变量。程序在原始 Cartesian 网格上以 d03 的 DX/DY 用 `numpy.gradient` 求导，再复用现有 polar 转换流程。

`bl_mass_convergence` 和 `bl_mfc` 默认使用100、200、…、1000 m。处理请求的 `options` 可改为：

```json
{"boundary_layer_bottom_m": 100, "boundary_layer_top_m": 1000}
```

也可指定不等间距层：

```json
{"boundary_layer_heights_m": [100, 250, 500, 750, 1000]}
```

## 一维诊断、云分类与辅助缓存

|变量|单位|计算或含义|
|---|---|---|
|`local_max_wind`|m s⁻¹|最低模式层风速的全域最大值。|
|`axis_mean_max_wind`|m s⁻¹|最低模式层切向风方位平均径向剖面的最大值。|
|`rmw`|km|方位平均切向风达到最大值的半径。|
|`r_34`|km|最低模式层方位平均切向风从 RMW 向外首次降至 34 kt（17.49 m s⁻¹）的半径；在相邻半径格点间线性插值。峰值不足 34 kt 或计算域内未降至阈值时为 NaN。|
|`center_slp_5km`|hPa|SLP中心5 km圆盘平均。|
|`center_land_distance`|km|台风中心到陆地边界的距离；全海洋成员按配置写入很大的替代值。|
|`precipitation_asymmetry`|1|基于 `RAINNCV_polar` 的四固定地理象限不对称指数。|
|`dbz_asymmetry`|1|基于当前高度中心 `dbz1_polar` 的同类不对称指数。|
|`shear_200_850_u`, `shear_200_850_v`|m s⁻¹|150–350格点半径环平均的 V200 minus V850 东、北分量。|
|`shear_200_850_magnitude`|m s⁻¹|上述两个分量的向量模。|
|`shear_200_850_direction`|degree|-degrees(atan2(Sv, Su))；需和切变相对方位图的约定一起解释。|
|`tilt_x`, `tilt_y`|km|上层中心减下层中心的东、北位移。默认2–8 km。|
|`tilt_magnitude`|km|tilt_x、tilt_y的向量模。|
|`tilt_angle`|degree|-degrees(atan2(tilt_y, tilt_x))，从东开始、顺时针为负。|
|`tilt_2_8`, `tilt_x_2_8`, `tilt_y_2_8`, `tilt_angle_2_8`|km / degree|2026-11-04 起 2–8 km 倾斜的新名字（与 2_4、2_6、2_10 一致），数值与上面三行旧名相同；旧名保留给旧数据和旧看板。|
|`classification`|类别代码|dbz/wa阈值分类：0无降水、1其他、2层状、3浅对流、4中等对流、5深对流。|
|`storm_motion` / `motion`|m s⁻¹、degree|中心轨迹的东、北平移速度、速度大小和方向；用于 `ua/va` 的平移订正。|
|`slp_center`, `layer_center`|中心文件|处理操作：分别生成SLP中心和指定高度中心，不是普通绘图场。|

`<variable>_polar` 是以SLP中心插值到 (azimuth, radius) 的缓存；`<variable>1_polar` 使用该高度 pressure 中心。`<variable>_asy_polar` 和 `<variable>_asy<N>w_polar` 是极坐标场的傅里叶非对称总量和第N波数分量，单位与原变量相同。

## 当前问题的推荐用法

比较180°与邻近风向时，先做 `bl_mfc` 填色，叠加500 m `convergence` 或 `wa`，并取LS 225°–315°、20–80 km的面积平均时间序列。若180°的 `bl_mfc` 峰值先于低层上升和降水峰值，且幅度更大，就能直接支持“低层来流改变摩擦辐合与水汽汇聚位置”的解释。

### 组合反射率非对称度

`mdbz_asymmetry` 使用以海平面气压中心为圆心的 `mdbz_polar`，沿用现有四个固定地理象限、面积加权的非对称度算法，每时次保存一个无量纲数值。负 dBZ 和缺测按现有算法置零，直接对 dBZ 统计，不转换为线性 Z。全圆无正值时结果为 NaN。

在 `处理数据/process_data.py` 设置 `VARIABLES = ["mdbz_asymmetry"]` 和 `ASYMMETRY_RADIUS_KM = 200.0`；半径表示从中心到该距离的完整圆盘。依赖数据自动准备；改变半径后同名非对称度输出会重算覆盖，不同时保留多个半径版本。
