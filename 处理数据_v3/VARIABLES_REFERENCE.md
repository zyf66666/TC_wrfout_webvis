# 处理数据 v3：变量完整说明

本文逐项说明用户列出的 **93 个变量**，并在附录补充当前 v3 注册表中的其他固定变量、内部依赖和入口脚本引用的变量。
单位、直接依赖和处理器按当前工作区的 v3 注册表核对；“依赖”列只列**直接依赖**，不是递归展开后的全部 WRF 字段。

## 命名和计算口径

- `*_polar`：以海平面气压中心 `slp_center` 为原点，将二维场插值到“方位角 × 半径”网格；`*1_polar`：改用当前高度的 `layer_center`。数字 `1` **不表示第 1 层**。同一源场可以有两种中心版本。
- `*_sr`（2026-11-03 约定）：与风速有关的变量用**风暴相对风**计算，即 `ua`、`va` 先减去风暴移速（`center_geo` 在 t ± 3 h 的中心差分）。`*_sr1` 以当前高度 `layer_center` 分解，`*_sr` 以 `slp_center` 分解；`*_sr_polar`、`*_sr1_polar` 依次以两种中心插值。不带 `_sr` 的 `ua`、`va`、`vr`、`vt`、`inflow_angle` 等都是相对地面的风。辐合、相对涡度减去均匀移速后不变，所以没有 `_sr` 版本；最低模式层的轴平均最大风、`rmw` 用整圈平均的切向风，基本不变，也没有。`rmw_by_height_sr`、`rmw_by_layer_center_sr` 应用户要求另设（分析套餐与风有关的量全部用风暴相对风），数值通常与原版几乎相同。
- `*_asy_polar`：在每个半径处减去该半径的方位平均，保留非轴对称部分；这与表中的三个**四象限标量非对称指数**不同。
- `height` 类三维 WRF 场先通过 `wrf.getvar()` 或原生位涡处理器取得模式层数据，再通过 `z` 与 `wrf.interplevel()` 插值到请求的海拔高度（MSL，m）；地面和标量变量通常每时次只保存一个切片。
- 除 `pvo` 保留高度插值缺测外，直接读取变量在保存前按 v3 读取器规则填补 NaN。所有 v3 `data` 均保存为 `float32`；`classification` 的类别数值也按此格式保存。
- 以下物理含义描述的是代码实际计算量。例如 `H_DIABATIC` 是**微物理潜热加热率**；`RAINNCV` 是**单模式时间步**降水量；`rmw` 使用最低模式层。
- 单位为 `mixed` 的 `motion` / `storm_motion` 是四分量向量 `[u速度, v速度, 合速度, 方向]`；前三项单位为 m s⁻¹，方向单位为度。`center_geo`、`land_boundary` 的经纬度分量单位为度。
- `dbz_asymmetry`、`mdbz_asymmetry`、`precipitation_asymmetry` 的四象限指数使用各象限半径加权积分份额 `fᵢ`，计算 `√[(4/3)Σ(fᵢ−1/4)²]`；它们与逐半径减去方位平均的 `*_asy_polar` 不同。

## 1. WRF 直接读取与原生诊断（22 个）

本节“依赖”列列 WRF 原始字段。前 21 个由 v3 的 `getvar` 读取器处理；`pvo` 由原生处理器从 WRF 字段计算。

| 变量名 | 单位 | 依赖 | 物理含义 | 计算方式 |
| --- | --- | --- | --- | --- |
| `HFX` | W m-2 | `HFX` | 地表向上的感热通量。 | 用 `getvar("HFX")` 提取二维场。 |
| `LH` | W m-2 | `LH` | 地表向上的潜热通量。 | 用 `getvar("LH")` 提取二维场。 |
| `PBLH` | m | `PBLH` | 行星边界层高度。 | 用 `getvar("PBLH")` 提取二维场；取值由 WRF 所选边界层方案产生。 |
| `QVAPOR` | kg kg-1 | `QVAPOR` | 水汽混合比。 | 用 `getvar("QVAPOR")` 提取模式层场，再插值到请求高度。 |
| `RAINNC` | mm | `RAINNC` | 自模拟起始或累计量重置以来的网格尺度降水累计量。 | 用 `getvar("RAINNC")` 提取二维累计场；v3 不做时间差分。 |
| `RAINNCV` | mm | `RAINNCV` | 单个模式时间步的非对流降水量。 | 用 `getvar("RAINNCV")` 提取二维场；v3 不将其换算成 mm h⁻¹。 |
| `U10` | m s-1 | `U10` | 10 m 高度的 U 风分量。 | 用 `getvar("U10")` 提取二维场。 |
| `UST` | m s-1 | `UST` | 摩擦速度 u\*，表征地表切应力的速度尺度。 | 用 `getvar("UST")` 提取二维场。 |
| `V10` | m s-1 | `V10` | 10 m 高度的 V 风分量。 | 用 `getvar("V10")` 提取二维场。 |
| `cape` | J kg-1 | `T`, `P`, `PB`, `QVAPOR`, `PH`, `PHB`, `HGT`, `PSFC` | 二维最大对流有效位能（MCAPE），表示气块可获得的正浮力能量。 | `getvar("cape_2d")` 会给出 MCAPE、MCIN、LCL、LFC 四个产品；v3 **只保存第一个 MCAPE**。 |
| `dbz` | dBZ | `T`, `P`, `PB`, `QVAPOR`, `QRAIN`；若存在，还使用 `QSNOW`, `QGRAUP` | 模拟雷达反射率，反映水凝物对雷达回波的贡献。 | `getvar("dbz")` 从温度、气压及水凝物混合比诊断三维反射率，再按高度插值；缺少的雪/霰字段按零处理。 |
| `diabatic_heating` | K s-1 | `H_DIABATIC` | **微物理潜热加热率**，并非所有物理过程的总非绝热加热率。 | v3 将此名称映射为 `getvar("H_DIABATIC")`，再按高度插值。 |
| `mdbz` | dBZ | 与 `dbz` 相同 | 柱内最大模拟雷达反射率。 | `getvar("mdbz")` 先计算三维 `dbz`，再沿垂直方向取最大值，输出二维场。 |
| `pressure` | hPa | `P`, `PB` | 完整模式气压。 | `getvar("pressure")` 计算 `(P + PB) / 100`，再按高度插值。 |
| `pvo` | PVU | 原生交错网格 `U`, `V`，以及 `T`, `P`, `PB`, `F`, `DX`, `DY`；无 `F` 时按配置读取同试验 `wrfinput_d01` 中的常数 `F` | Ertel 位涡，结合绝对涡度与位温的三维梯度描述旋转、层结结构。 | `wrf.getvar("pvo")` 本身可诊断位涡；**v3 实际调用自己的 `variables/pvo.py:calculate`**，在模式层上使用 `wrf.pvo()` 数值核、完整位温 `T+300 K`、完整气压 `P+PB`、原始交错风和 `F`，并将三个地图比例因子固定为 1，之后才按高度插值。该口径与直接调用 `getvar("pvo")` 的默认地图比例因子口径不一定相同。 |
| `rh` | % | `T`, `P`, `PB`, `QVAPOR` | 相对湿度，即水汽含量相对于该温压下饱和值的比例。 | `getvar("rh")` 由温度、完整气压和水汽混合比计算，再按高度插值。 |
| `slp` | hPa | `T`, `P`, `PB`, `QVAPOR`, `PH`, `PHB` | 海平面气压。 | `getvar("slp")` 使用三维温度、气压、水汽和位势高度将近地面气压折算到海平面，输出二维场。 |
| `temp` | K | `T`, `P`, `PB` | 绝对气温，不是 WRF 原始变量 `T` 所表示的扰动位温。 | `getvar("temp", units="K")` 先取完整位温 `T + 300 K`，再利用完整气压按泊松关系求温度，最后按高度插值。 |
| `theta_e` | K | `T`, `P`, `PB`, `QVAPOR` | 相当位温，用于表征空气的热力和水汽状态。 | `getvar("theta_e", units="K")` 从温度、完整气压和水汽混合比诊断，再按高度插值。 |
| `ua` | m s-1 | `U` | 位于质量网格点的模型网格 U 风分量。 | `getvar("ua", units="m s-1")` 对交错网格 `U` 做水平去交错，再按高度插值；这里未旋转成地理正东风。 |
| `va` | m s-1 | `V` | 位于质量网格点的模型网格 V 风分量。 | `getvar("va", units="m s-1")` 对交错网格 `V` 做水平去交错，再按高度插值；这里未旋转成地理正北风。 |
| `wa` | m s-1 | `W` | 位于质量网格点的垂直速度。 | `getvar("wa", units="m s-1")` 对垂直交错网格 `W` 去交错，再按高度插值。 |

## 2. 极坐标、方位非对称与兼容名称（37 个）

| 变量名 | 单位 | 依赖 | 物理含义 | 计算方式 |
| --- | --- | --- | --- | --- |
| `HFX_polar` | W m-2 | `HFX`、`slp_center` | 以 `slp_center` 为原点表示的 地表向上的感热通量（极坐标场） | 读取 `HFX` 与中心，按实际网格间距双线性插值为 360 个方位角 × 半径坐标；半径受边界约束。 |
| `LH_polar` | W m-2 | `LH`、`slp_center` | 以 `slp_center` 为原点表示的 地表向上的潜热通量（极坐标场） | 读取 `LH` 与中心，按实际网格间距双线性插值为 360 个方位角 × 半径坐标；半径受边界约束。 |
| `QVAPOR_polar` | kg kg-1 | `QVAPOR`、`slp_center` | 以 `slp_center` 为原点表示的 水汽混合比（极坐标场） | 读取 `QVAPOR` 与中心，按实际网格间距双线性插值为 360 个方位角 × 半径坐标；半径受边界约束。 |
| `RAINNCV_polar` | mm | `RAINNCV`、`slp_center` | 以 `slp_center` 为原点表示的 单个模式时间步的非对流降水量（极坐标场） | 读取 `RAINNCV` 与中心，按实际网格间距双线性插值为 360 个方位角 × 半径坐标；半径受边界约束。 |
| `bl_mfc_polar` | kg m-2 s-1 | `bl_mfc`、`slp_center` | 以 `slp_center` 为原点表示的 边界层整层水汽质量通量辐合（极坐标场） | 读取 `bl_mfc` 与中心，按实际网格间距双线性插值为 360 个方位角 × 半径坐标；半径受边界约束。 |
| `convergence_polar` | s-1 | `convergence`、`slp_center` | 以 `slp_center` 为原点表示的 水平风场辐合（极坐标场） | 读取 `convergence` 与中心，按实际网格间距双线性插值为 360 个方位角 × 半径坐标；半径受边界约束。 |
| `dbz1_polar` | dBZ | `dbz`、`layer_center` | 以 逐高度 `layer_center` 为原点表示的 模拟雷达反射率，反映水凝物对雷达回波的贡献（极坐标场） | 读取 `dbz` 与中心，按实际网格间距双线性插值为 360 个方位角 × 半径坐标；半径受边界约束。 |
| `dbz_polar` | dBZ | `dbz`、`slp_center` | 以 `slp_center` 为原点表示的 模拟雷达反射率，反映水凝物对雷达回波的贡献（极坐标场） | 读取 `dbz` 与中心，按实际网格间距双线性插值为 360 个方位角 × 半径坐标；半径受边界约束。 |
| `density1_polar` | kg m-3 | `density`、`layer_center` | 以 逐高度 `layer_center` 为原点表示的 湿空气密度（极坐标场） | 读取 `density` 与中心，按实际网格间距双线性插值为 360 个方位角 × 半径坐标；半径受边界约束。 |
| `density_polar` | kg m-3 | `density`、`slp_center` | 以 `slp_center` 为原点表示的 湿空气密度（极坐标场） | 读取 `density` 与中心，按实际网格间距双线性插值为 360 个方位角 × 半径坐标；半径受边界约束。 |
| `diabatic_heating1_polar` | K s-1 | `diabatic_heating`、`layer_center` | 以 逐高度 `layer_center` 为原点表示的 **微物理潜热加热率**，并非所有物理过程的总非绝热加热率（极坐标场） | 读取 `diabatic_heating` 与中心，按实际网格间距双线性插值为 360 个方位角 × 半径坐标；半径受边界约束。 |
| `inflow_angle_polar` | degree | `inflow_angle`、`slp_center` | 以 `slp_center` 为原点表示的 径向流入相对切向风的夹角（极坐标场） | 读取 `inflow_angle` 与中心，按实际网格间距双线性插值为 360 个方位角 × 半径坐标；半径受边界约束。 |
| `land_distance` | km | `center_land_distance` | `center_land_distance` 的兼容名称：风暴中心到海岸的有符号最近距离 | 读取 `center_land_distance` 并以 `land_distance` 保存；数值不另算。 |
| `mdbz_polar` | dBZ | `mdbz`、`slp_center` | 以 `slp_center` 为原点表示的 柱内最大模拟雷达反射率（极坐标场） | 读取 `mdbz` 与中心，按实际网格间距双线性插值为 360 个方位角 × 半径坐标；半径受边界约束。 |
| `motion` | mixed | `storm_motion` | `storm_motion` 的兼容名称：风暴中心平移速度和方向向量 | 读取 `storm_motion` 并以 `motion` 保存；数值不另算。 |
| `pvo1_polar` | PVU | `pvo`、`layer_center` | 以 逐高度 `layer_center` 为原点表示的 Ertel 位涡，结合绝对涡度与位温的三维梯度描述旋转、层结结构（极坐标场） | 读取 `pvo` 与中心，按实际网格间距双线性插值为 360 个方位角 × 半径坐标；半径受边界约束。 |
| `pvo_polar` | PVU | `pvo`、`slp_center` | 以 `slp_center` 为原点表示的 Ertel 位涡，结合绝对涡度与位温的三维梯度描述旋转、层结结构（极坐标场） | 读取 `pvo` 与中心，按实际网格间距双线性插值为 360 个方位角 × 半径坐标；半径受边界约束。 |
| `qflux_r_polar` | kg m-2 s-1 | `qflux_r`、`slp_center` | 以 `slp_center` 为原点表示的 相对 SLP 中心的径向水汽质量通量（极坐标场） | 读取 `qflux_r` 与中心，按实际网格间距双线性插值为 360 个方位角 × 半径坐标；半径受边界约束。 |
| `rh1_polar` | % | `rh`、`layer_center` | 以 逐高度 `layer_center` 为原点表示的 相对湿度，即水汽含量相对于该温压下饱和值的比例（极坐标场） | 读取 `rh` 与中心，按实际网格间距双线性插值为 360 个方位角 × 半径坐标；半径受边界约束。 |
| `rh_polar` | % | `rh`、`slp_center` | 以 `slp_center` 为原点表示的 相对湿度，即水汽含量相对于该温压下饱和值的比例（极坐标场） | 读取 `rh` 与中心，按实际网格间距双线性插值为 360 个方位角 × 半径坐标；半径受边界约束。 |
| `theta_e1_asy_polar` | K | `theta_e1_polar` | `theta_e1_polar` 的方位非对称分量 | 在每个半径处从 `theta_e1_polar` 减去 360 个方位角的均值。 |
| `theta_e1_polar` | K | `theta_e`、`layer_center` | 以 逐高度 `layer_center` 为原点表示的 相当位温，用于表征空气的热力和水汽状态（极坐标场） | 读取 `theta_e` 与中心，按实际网格间距双线性插值为 360 个方位角 × 半径坐标；半径受边界约束。 |
| `theta_e_anomaly1_polar` | K | `theta_e_anomaly`、`layer_center` | 以 逐高度 `layer_center` 为原点表示的 相当位温相对环境的异常（极坐标场） | 读取 `theta_e_anomaly` 与中心，按实际网格间距双线性插值为 360 个方位角 × 半径坐标；半径受边界约束。 |
| `theta_e_asy_polar` | K | `theta_e_polar` | `theta_e_polar` 的方位非对称分量 | 在每个半径处从 `theta_e_polar` 减去 360 个方位角的均值。 |
| `theta_e_polar` | K | `theta_e`、`slp_center` | 以 `slp_center` 为原点表示的 相当位温，用于表征空气的热力和水汽状态（极坐标场） | 读取 `theta_e` 与中心，按实际网格间距双线性插值为 360 个方位角 × 半径坐标；半径受边界约束。 |
| `ua_polar` | m s-1 | `ua`、`slp_center` | 以 `slp_center` 为原点表示的 位于质量网格点的模型网格 U 风分量（极坐标场） | 读取 `ua` 与中心，按实际网格间距双线性插值为 360 个方位角 × 半径坐标；半径受边界约束。 |
| `va_polar` | m s-1 | `va`、`slp_center` | 以 `slp_center` 为原点表示的 位于质量网格点的模型网格 V 风分量（极坐标场） | 读取 `va` 与中心，按实际网格间距双线性插值为 360 个方位角 × 半径坐标；半径受边界约束。 |
| `vr1_asy_polar` | m s-1 | `vr1_polar` | `vr1_polar` 的方位非对称分量 | 在每个半径处从 `vr1_polar` 减去 360 个方位角的均值。 |
| `vr1_polar` | m s-1 | `vr1`、`layer_center` | 以 逐高度 `layer_center` 为原点表示的 相对逐高度气压中心的径向风（极坐标场） | 读取 `vr1` 与中心，按实际网格间距双线性插值为 360 个方位角 × 半径坐标；半径受边界约束。 |
| `vr_asy_polar` | m s-1 | `vr_polar` | `vr_polar` 的方位非对称分量 | 在每个半径处从 `vr_polar` 减去 360 个方位角的均值。 |
| `vr_polar` | m s-1 | `vr`、`slp_center` | 以 `slp_center` 为原点表示的 相对海平面气压中心的径向风（极坐标场） | 读取 `vr` 与中心，按实际网格间距双线性插值为 360 个方位角 × 半径坐标；半径受边界约束。 |
| `vt_polar` | m s-1 | `vt`、`slp_center` | 以 `slp_center` 为原点表示的 相对海平面气压中心的切向风（极坐标场） | 读取 `vt` 与中心，按实际网格间距双线性插值为 360 个方位角 × 半径坐标；半径受边界约束。 |
| `wa1_asy_polar` | m s-1 | `wa1_polar` | `wa1_polar` 的方位非对称分量 | 在每个半径处从 `wa1_polar` 减去 360 个方位角的均值。 |
| `wa1_polar` | m s-1 | `wa`、`layer_center` | 以 逐高度 `layer_center` 为原点表示的 位于质量网格点的垂直速度（极坐标场） | 读取 `wa` 与中心，按实际网格间距双线性插值为 360 个方位角 × 半径坐标；半径受边界约束。 |
| `wa_asy_polar` | m s-1 | `wa_polar` | `wa_polar` 的方位非对称分量 | 在每个半径处从 `wa_polar` 减去 360 个方位角的均值。 |
| `wa_polar` | m s-1 | `wa`、`slp_center` | 以 `slp_center` 为原点表示的 位于质量网格点的垂直速度（极坐标场） | 读取 `wa` 与中心，按实际网格间距双线性插值为 360 个方位角 × 半径坐标；半径受边界约束。 |
| `wind_speed_10_polar` | m s-1 | `wind_speed_10`、`slp_center` | 以 `slp_center` 为原点表示的 10 m 水平风速（极坐标场） | 读取 `wind_speed_10` 与中心，按实际网格间距双线性插值为 360 个方位角 × 半径坐标；半径受边界约束。 |

## 3. 其他派生诊断（56 个）

| 变量名 | 单位 | 依赖 | 物理含义 | 计算方式 |
| --- | --- | --- | --- | --- |
| `axis_mean_max_wind` | m s-1 | `ua`（最低模式层）、`va`（最低模式层）、`slp_center` | 最低模式层的最大轴对称切向风速 | 相对 `slp_center` 计算切向风，按半径求方位平均，再取剖面最大值。 |
| `bl_mfc` | kg m-2 s-1 | `mfc`（100 m）、`mfc`（200 m）、`mfc`（300 m）、`mfc`（400 m）、`mfc`（500 m）、`mfc`（600 m）、`mfc`（700 m）、`mfc`（800 m）、`mfc`（900 m）、`mfc`（1000 m） | 边界层整层水汽质量通量辐合 | 将各指定高度的 `mfc` 以高度为坐标做梯形积分；默认积分 100–1000 m。 |
| `center_land_distance` | km | `center_geo`、`land_boundary`（d01）（t=0 min） | 风暴中心到海岸的有符号最近距离 | 用中心经纬度和 d01 参考时次的海岸点计算球面最短距离；中心经度小于海岸点平均经度时取负。 |
| `center_slp_5km` | hPa | `slp`、`slp_center` | 风暴中心周围 5 km 内的平均海平面气压 | 以 `slp_center` 为圆心，取半径不大于 5 km 的 `slp` 格点均值。 |
| `classification` | 1 | `dbz`（900 m）、`dbz`（1000 m）、`dbz`（1250 m）、`dbz`（1500 m）、`dbz`（1750 m）、`dbz`（2000 m）、`dbz`（3000 m）、`dbz`（4000 m）、`dbz`（8000 m）、`wa`（900 m）、`wa`（1000 m）、`wa`（1250 m）、`wa`（1500 m）、`wa`（1750 m）、`wa`（2000 m）、`wa`（3000 m）、`wa`（4000 m）、`wa`（8000 m） | 基于回波和垂直速度的云/降水类型，类别值 0–5 | 读取 9 个固定高度的 `dbz`、`wa` 并按回波与上升速度阈值判别；0=背景，1=其他回波，2=层状，3=浅对流，4=中对流，5=深对流。类别值存入 float32 NetCDF。 |
| `convergence` | s-1 | `ua`、`va` | 水平风场辐合 | 按网格间距计算 `-(∂ua/∂x + ∂va/∂y)`。 |
| `dbz_asymmetry` | 1 | `dbz1_polar`（3000 m） | 指定高度雷达回波的固定地理四象限非对称指数 | 读取默认 3000 m 的 `dbz1_polar`，在指定半径内对四象限做半径加权积分，比较四象限份额与 1/4 的差异。 |
| `density` | kg m-3 | `pressure`、`temp`、`QVAPOR` | 湿空气密度 | 由 `pressure`、`temp`、`QVAPOR` 调用 `metpy.calc.density`，并填补缺测。 |
| `inflow_angle` | degree | `ua`、`va`、`slp_center` | 径向流入相对切向风的夹角 | 相对 `slp_center` 投影成径向风 `vr` 与切向风 `vt`，计算 `atan2(-vr, vt)` 并转成角度。 |
| `local_max_wind` | m s-1 | `ua`（最低模式层）、`va`（最低模式层） | 最低模式层整个二维区域中的最大水平风速 | 在最低层逐格计算 `√(ua²+va²)`，再取全场最大值；不做方位平均。 |
| `mass_convergence` | kg m-3 s-1 | `ua`、`va`、`pressure`、`temp`、`QVAPOR` | 水平质量通量辐合 | 先由气压、气温、水汽混合比求密度 `ρ`，再算 `-∇·(ρu,ρv)`。 |
| `mdbz_asymmetry` | 1 | `mdbz_polar` | 柱最大雷达回波的固定地理四象限非对称指数 | 对 `mdbz_polar` 在指定半径内做四象限半径加权积分，比较各象限份额与 1/4 的差异。 |
| `mfc` | kg m-3 s-1 | `ua`、`va`、`pressure`、`temp`、`QVAPOR` | 水平水汽质量通量辐合 | 令 `q=QVAPOR`，由气压、气温求密度 `ρ`，计算 `-∇·(ρqu,ρqv)`。 |
| `precipitation_asymmetry` | 1 | `RAINNCV_polar` | 单步非对流降水的固定地理四象限非对称指数 | 对 `RAINNCV_polar` 在指定半径内做四象限半径加权积分，比较各象限份额与 1/4 的差异。 |
| `qflux_r` | kg m-2 s-1 | `ua`、`va`、`pressure`、`temp`、`QVAPOR`、`slp_center` | 相对 SLP 中心的径向水汽质量通量 | 先求密度 `ρ` 和径向风 `vr`，再算 `ρ × QVAPOR × vr`；径向向外为正。 |
| `r_34` | km | `ua`（最低模式层）、`va`（最低模式层）、`slp_center` | 最低模式层的 34 kt 风圈外缘半径 | 对切向风做方位平均，从最大值所在半径向外搜索首次降到 34 kt 的位置，并在相邻半径间线性插值；未达到阈值或未穿过阈值时返回 NaN。 |
| `rmw` | km | `ua`（最低模式层）、`va`（最低模式层）、`slp_center` | 最低模式层最大方位平均切向风半径 | 相对 `slp_center` 求最低模式层切向风的径向方位平均剖面，取峰值半径。 |
| `shear_200_850_direction` | degree | d02 `_ua_upper`、`_ua_lower`、`_va_upper`、`_va_lower`、`slp_center` | 200–850 hPa 环境风切变方向角 | 在 d02 中心 150–350 km 环带求高低层风均值，再计算 `-atan2(Δv,Δu)` 的角度，沿用项目既有角度口径。 |
| `shear_200_850_magnitude` | m s-1 | d02 `_ua_upper`、`_ua_lower`、`_va_upper`、`_va_lower`、`slp_center` | 200–850 hPa 环境风切变强度 | 对 d02 中心 150–350 km 环带平均后的高低层 U、V 差向量求 `√(Δu²+Δv²)`。 |
| `shear_200_850_u` | m s-1 | d02 `_ua_upper`、`_ua_lower`、`_va_upper`、`_va_lower`、`slp_center` | 200–850 hPa 环境风切变的 U 分量 | 将 d02 的 200/850 hPa U 风按物理距离插值到极坐标，在中心 150–350 km 环带求均值，计算高层 U 减低层 U。 |
| `shear_200_850_v` | m s-1 | d02 `_ua_upper`、`_ua_lower`、`_va_upper`、`_va_lower`、`slp_center` | 200–850 hPa 环境风切变的 V 分量 | 将 d02 的 200/850 hPa V 风按物理距离插值到极坐标，在中心 150–350 km 环带求均值，计算高层 V 减低层 V。 |
| `shear_core_200_850_u` | m s-1 | `_ua_upper`、`_ua_lower`、`_va_upper`、`_va_lower`、`slp_center`（均为目标域，处理套餐用 d03） | 内核区 200–850 hPa 风切变的 U 分量（2026-11-02） | 以同一时次目标域的 `slp_center` 为圆心，把 200/850 hPa U 风按物理距离插值到极坐标，在 150–350 km 环带求均值后相减；与环境切变同一算法，只是来源为目标域而非 d02。 |
| `shear_core_200_850_v` | m s-1 | `_ua_upper`、`_ua_lower`、`_va_upper`、`_va_lower`、`slp_center`（均为目标域，处理套餐用 d03） | 内核区 200–850 hPa 风切变的 V 分量 | 同 `shear_core_200_850_u`，对 V 风计算。 |
| `shear_core_200_850_magnitude` | m s-1 | `_ua_upper`、`_ua_lower`、`_va_upper`、`_va_lower`、`slp_center`（均为目标域，处理套餐用 d03） | 内核区 200–850 hPa 风切变强度 | 对 150–350 km 环带平均后的高低层 U、V 差向量求 `√(Δu²+Δv²)`。 |
| `shear_core_200_850_direction` | degree | `_ua_upper`、`_ua_lower`、`_va_upper`、`_va_lower`、`slp_center`（均为目标域，处理套餐用 d03） | 内核区 200–850 hPa 风切变方向角 | `-atan2(Δv,Δu)` 转为角度，与 `shear_200_850_direction` 同一约定。环带外缘 350 km 接近 d03 移动嵌套边界时缺测格点不参与平均。 |
| `theta_e_anomaly` | K | `theta_e`、`theta_e1_polar`（d02） | 相当位温相对环境的异常 | 目标域 `theta_e` 减去环境域同高度 `theta_e1_polar` 在默认 400–500 km 环带的半径加权均值。 |
| `tilt_angle` | degree | `layer_center`（2000 m）、`layer_center`（8000 m） | 高层中心相对低层中心的倾斜方向（旧名，同 `tilt_angle_2_8`） | 由中心位移计算 `-atan2(tilt_y,tilt_x)` 并转成角度，以正东为零方向。 |
| `tilt_magnitude` | km | `layer_center`（2000 m）、`layer_center`（8000 m） | 高低层风暴中心的水平倾斜距离（旧名，同 `tilt_2_8`） | 对 `tilt_x`、`tilt_y` 的同口径位移取欧氏长度。 |
| `tilt_x` | km | `layer_center`（2000 m）、`layer_center`（8000 m） | 高低层风暴中心的东西位移（旧名，同 `tilt_x_2_8`） | 默认取 8000 m 与 2000 m 的 `layer_center`，计算 `(x高-x低)×dx`。 |
| `tilt_y` | km | `layer_center`（2000 m）、`layer_center`（8000 m） | 高低层风暴中心的南北位移（旧名，同 `tilt_y_2_8`） | 默认取 8000 m 与 2000 m 的 `layer_center`，计算 `(y高-y低)×dy`。 |
| `tilt_1_6` | km | `layer_center`（1000 m）、`layer_center`（6000 m） | 1–6 km 的水平倾斜距离 | 固定取 6000 m 相对 1000 m 中心的位移长度，与原有 `tilt_*` 独立保存。 |
| `tilt_x_1_6` | km | `layer_center`（1000 m）、`layer_center`（6000 m） | 1–6 km 的东西位移 | `(x6000-x1000)×dx`，高度不受通用倾斜参数影响。 |
| `tilt_y_1_6` | km | `layer_center`（1000 m）、`layer_center`（6000 m） | 1–6 km 的南北位移 | `(y6000-y1000)×dy`，高度不受通用倾斜参数影响。 |
| `tilt_angle_1_6` | degree | `layer_center`（1000 m）、`layer_center`（6000 m） | 1–6 km 的倾斜方向 | `-atan2(tilt_y_1_6,tilt_x_1_6)` 转为角度，以正东为零方向。 |
| `tilt_2_4` | km | `layer_center`（2000 m）、`layer_center`（4000 m） | 2–4 km 的水平倾斜距离 | 固定取 4000 m 相对 2000 m 中心的位移长度，与 `tilt_1_6` 同算法，独立保存。 |
| `tilt_x_2_4` | km | `layer_center`（2000 m）、`layer_center`（4000 m） | 2–4 km 的东西位移 | `(x4000-x2000)×dx`。 |
| `tilt_y_2_4` | km | `layer_center`（2000 m）、`layer_center`（4000 m） | 2–4 km 的南北位移 | `(y4000-y2000)×dy`。 |
| `tilt_angle_2_4` | degree | `layer_center`（2000 m）、`layer_center`（4000 m） | 2–4 km 的倾斜方向 | `-atan2(tilt_y_2_4,tilt_x_2_4)` 转为角度，以正东为零方向。 |
| `tilt_2_6` | km | `layer_center`（2000 m）、`layer_center`（6000 m） | 2–6 km 的水平倾斜距离 | 固定取 6000 m 相对 2000 m 中心的位移长度，与 `tilt_1_6` 同算法，独立保存。 |
| `tilt_x_2_6` | km | `layer_center`（2000 m）、`layer_center`（6000 m） | 2–6 km 的东西位移 | `(x6000-x2000)×dx`。 |
| `tilt_y_2_6` | km | `layer_center`（2000 m）、`layer_center`（6000 m） | 2–6 km 的南北位移 | `(y6000-y2000)×dy`。 |
| `tilt_angle_2_6` | degree | `layer_center`（2000 m）、`layer_center`（6000 m） | 2–6 km 的倾斜方向 | `-atan2(tilt_y_2_6,tilt_x_2_6)` 转为角度，以正东为零方向。 |
| `tilt_2_8` | km | `layer_center`（2000 m）、`layer_center`（8000 m） | 2–8 km 的水平倾斜距离 | 固定取 8000 m 相对 2000 m 中心的位移长度；2026-11-04 起取代旧名 `tilt_magnitude`（数值相同）。 |
| `tilt_x_2_8` | km | `layer_center`（2000 m）、`layer_center`（8000 m） | 2–8 km 的东西位移 | `(x8000-x2000)×dx`；取代旧名 `tilt_x`。 |
| `tilt_y_2_8` | km | `layer_center`（2000 m）、`layer_center`（8000 m） | 2–8 km 的南北位移 | `(y8000-y2000)×dy`；取代旧名 `tilt_y`。 |
| `tilt_angle_2_8` | degree | `layer_center`（2000 m）、`layer_center`（8000 m） | 2–8 km 的倾斜方向 | `-atan2(tilt_y_2_8,tilt_x_2_8)` 转为角度，以正东为零方向；取代旧名 `tilt_angle`。 |
| `tilt_2_10` | km | `layer_center`（2000 m）、`layer_center`（10000 m） | 2–10 km 的水平倾斜距离 | 固定取 10000 m 相对 2000 m 中心的位移长度，与 `tilt_1_6` 同算法，独立保存。 |
| `tilt_x_2_10` | km | `layer_center`（2000 m）、`layer_center`（10000 m） | 2–10 km 的东西位移 | `(x10000-x2000)×dx`。 |
| `tilt_y_2_10` | km | `layer_center`（2000 m）、`layer_center`（10000 m） | 2–10 km 的南北位移 | `(y10000-y2000)×dy`。 |
| `tilt_angle_2_10` | degree | `layer_center`（2000 m）、`layer_center`（10000 m） | 2–10 km 的倾斜方向 | `-atan2(tilt_y_2_10,tilt_x_2_10)` 转为角度，以正东为零方向。 |
| `vr` | m s-1 | `ua`、`va`、`slp_center` | 相对海平面气压中心的径向风 | 把 `ua`、`va` 投影到以 `slp_center` 为原点的径向方向；向外为正。 |
| `vr1` | m s-1 | `ua`、`va`、`layer_center` | 相对逐高度气压中心的径向风 | 把 `ua`、`va` 投影到以当前高度 `layer_center` 为原点的径向方向；向外为正。 |
| `vr1_ventilation_polar` | kg K m-2 s-1 | `density1_polar`、`theta_e1_asy_polar`、`vr1_polar`、`vr1_asy_polar` | 以逐高度中心表示的径向通风诊断量 | 在极坐标上计算 `density1_polar × theta_e1_asy_polar × vr1_asy_polar`；原始 `vr1_polar>0`（向外）处置零。 |
| `vr_ventilation_polar` | kg K m-2 s-1 | `density_polar`、`theta_e_asy_polar`、`vr_polar`、`vr_asy_polar` | 以 SLP 中心表示的径向通风诊断量 | 在极坐标上计算 `ρ × θe非对称量 × vr非对称量`；原始 `vr>0`（向外）处置零。 |
| `vt` | m s-1 | `ua`、`va`、`slp_center` | 相对海平面气压中心的切向风 | 把 `ua`、`va` 投影到以 `slp_center` 为原点的逆时针切向方向。 |
| `vr_sr` | m s-1 | `ua`、`va`、`slp_center`、`center_geo`（t ± 3 h） | 风暴相对径向风（相对海平面中心，向外为正；2026-11-03） | `ua`、`va` 减去风暴移速后投影到以 `slp_center` 为原点的径向。移速：`center_geo` 在 t ± 3 h 的中心差分（与超梯度诊断同一口径），开头缺 t − 3 h 时向前差分、末尾缺 t + 3 h 时向后差分，差分方式写入元数据 `motion_method`。 |
| `vt_sr` | m s-1 | `ua`、`va`、`slp_center`、`center_geo`（t ± 3 h） | 风暴相对切向风（相对海平面中心，逆时针为正） | 同 `vr_sr`，投影到逆时针切向。 |
| `vr_sr1` | m s-1 | `ua`、`va`、`layer_center`、`center_geo`（t ± 3 h） | 风暴相对径向风（相对当前高度气压中心，向外为正） | 同 `vr_sr`，以当前高度 `layer_center` 为原点；`vr_sr1_polar` 以同一中心插值。 |
| `vt_sr1` | m s-1 | `ua`、`va`、`layer_center`、`center_geo`（t ± 3 h） | 风暴相对切向风（相对当前高度气压中心，逆时针为正） | 同 `vt_sr`，以当前高度 `layer_center` 为原点。 |
| `ua_sr` | m s-1 | `ua`、`center_geo`（t ± 3 h） | 笛卡尔坐标的风暴相对东西风（2026-11-03） | `ua` 减去风暴移速的东西分量；移速口径同 `vr_sr`。 |
| `va_sr` | m s-1 | `va`、`center_geo`（t ± 3 h） | 笛卡尔坐标的风暴相对南北风 | `va` 减去风暴移速的南北分量。 |
| `inflow_angle_sr` | degree | `ua`、`va`、`slp_center`、`center_geo`（t ± 3 h） | 风暴相对入流角（相对海平面中心） | 用风暴相对风求 `vr_sr`、`vt_sr`，计算 `atan2(-vr_sr, vt_sr)` 并转成角度；公式同 `inflow_angle`。 |
| `inflow_angle_sr1` | degree | `ua`、`va`、`layer_center`、`center_geo`（t ± 3 h） | 风暴相对入流角（相对当前高度气压中心） | 同 `inflow_angle_sr`，以当前高度 `layer_center` 分解；`inflow_angle_sr1_polar` 以同一中心插值。 |
| `qflux_r_sr` | kg m-2 s-1 | `ua`、`va`、`pressure`、`temp`、`QVAPOR`、`slp_center`、`center_geo`（t ± 3 h） | 风暴相对径向水汽质量通量（相对海平面中心） | `ρ × QVAPOR × vr_sr`；径向向外为正；公式同 `qflux_r`。 |
| `qflux_r_sr1` | kg m-2 s-1 | `ua`、`va`、`pressure`、`temp`、`QVAPOR`、`layer_center`、`center_geo`（t ± 3 h） | 风暴相对径向水汽质量通量（相对当前高度气压中心） | `ρ × QVAPOR × vr_sr1`；`qflux_r_sr1_polar` 以同一中心插值。 |
| `vr_sr_ventilation_polar` | kg K m-2 s-1 | `density_polar`、`theta_e_asy_polar`、`vr_sr_polar`、`vr_sr_asy_polar` | 以 SLP 中心表示的径向通风诊断量（风暴相对径向风） | 同 `vr_ventilation_polar`，径向风换成 `vr_sr`；`vr_sr_polar>0`（向外）处置零。 |
| `vr_sr1_ventilation_polar` | kg K m-2 s-1 | `density1_polar`、`theta_e1_asy_polar`、`vr_sr1_polar`、`vr_sr1_asy_polar` | 以逐高度中心表示的径向通风诊断量（风暴相对径向风） | 同 `vr1_ventilation_polar`，径向风换成 `vr_sr1`。 |
| `rmw_by_height_sr` | km | `ua`、`va`、`slp_center`、`center_geo`（t ± 3 h） | 风暴相对切向风方位平均最大处的半径（相对海平面中心，每个高度一个数） | 同 `rmw_by_height`，切向风换成 `vt_sr`；元数据记录该处方位平均风速。 |
| `rmw_by_layer_center_sr` | km | `ua`、`va`、`layer_center`、`center_geo`（t ± 3 h） | 风暴相对切向风方位平均最大处的半径（相对当前高度气压中心） | 同 `rmw_by_layer_center`，切向风换成 `vt_sr1`。 |
| `w1_ventilation_polar` | kg K m-2 s-1 | `density1_polar`、`theta_e1_asy_polar`、`wa1_polar`、`wa1_asy_polar` | 以逐高度中心表示的下沉通风诊断量 | 在极坐标上计算 `density1_polar × theta_e1_asy_polar × wa1_asy_polar`；原始 `wa1_polar>0`（上升）处置零。 |
| `w_ventilation_polar` | kg K m-2 s-1 | `density_polar`、`theta_e_asy_polar`、`wa_polar`、`wa_asy_polar` | 以 SLP 中心表示的下沉通风诊断量 | 在极坐标上计算 `ρ × θe非对称量 × wa非对称量`；原始 `wa>0`（上升）处置零。 |
| `wind_speed_10` | m s-1 | `U10`、`V10` | 10 m 水平风速 | 逐格计算 `√(U10²+V10²)`。 |

## 4. 超梯度力诊断（5 个，2026-10-26 新增）

径向动量方程（风暴相对、等高面、f 平面，向外为正）：`Dvr/Dt = pgf_r + cen_r + cor_r + motf_r + 摩擦/湍流`。四项都以 `slp_center` 为原点、逐格点计算；`*_polar` 的方位平均因此是“先算力、再平均”（含涡动项），与 H19（看板 8817b4e6387e）同口径。风暴移速 `c` 由 `center_geo` 在 t ± `agradient_motion_half_window_h`（默认 3 h）的中心差分求得（适用于移动嵌套 d03，并压住地面中心逐时跳动）；`f` 取 `OPTIONS["coriolis_f"]`（本项目 16 个试验 d03 wrfout 的 `F` 均为 4.9881e-5 s⁻¹）；忽略风暴加速度项。处理需要 t−3 h 与 t+3 h 的中心，所以最早只能算到第 3 h、最晚到结束前 3 h。算法与测试：`wrf_processing_v3/variables/_agradient.py`、`tests/test_agradient.py`。

| 变量名 | 单位 | 依赖 | 物理含义 | 计算方式 |
| --- | --- | --- | --- | --- |
| `pgf_r` | m s-2 | `pressure`、`density`、`slp_center` | 气压梯度力的径向分量（向外为正；通常为负，指向中心） | 在等高面上对 `pressure`（换成 Pa）求水平梯度，投影到以 `slp_center` 为原点的径向：`−(1/ρ)(cosθ ∂p/∂x + sinθ ∂p/∂y)`。 |
| `cen_r` | m s-2 | `ua`、`va`、`slp_center`、`center_geo`（t ± 3 h） | 离心力项 v_t′²/r（v_t′ 为风暴相对切向风） | `ua`、`va` 先减去风暴移速，再投影成切向风后算 `vt′²/r`；中心 2 km 以内置为缺测。 |
| `cor_r` | m s-2 | `ua`、`va`、`slp_center`、`center_geo`（t ± 3 h） | 科氏力项 f·v_t′ | 同 `cen_r` 的风暴相对切向风乘以常数 f。 |
| `motf_r` | m s-2 | `slp`（只取网格形状）、`slp_center`、`center_geo`（t ± 3 h） | 匀速平移坐标系修正 f·c_t（二维，各高度相同）：科氏力作用在风暴移速上的部分，与环境地转气压梯度相抵 | `f(−c_x sinθ + c_y cosθ)`；幅度为 f·|c|（移速 4 m/s 时约 2×10⁻⁴ m s⁻²）。 |
| `agf_r` | m s-2 | `pgf_r`、`cen_r`、`cor_r`、`motf_r` | 超梯度力（径向非梯度风合力）：正 = 超梯度、净力向外，入流减速；负 = 次梯度 | 四项逐格相加。平移的梯度风平衡涡旋（含环境地转气压）合力为 0；配置已对 `ua/va` 做移动订正时，自动改读地球相对风以免重复减移速。 |

## 附录：另 27 个固定/内部/入口变量

本附录包括 93 个清单之外的注册表固定变量，以及入口脚本和别名规则中明确使用的名称。`_ua_*`、`_va_*` 是风切变的内部气压面输入。后缀规则还能生成其他组合，不能把附录理解为所有可能的动态名称。

| 变量名 | 单位 | 依赖 | 物理含义 | 计算方式 |
| --- | --- | --- | --- | --- |
| `_ua_lower` | m s-1 | `U`, `P`, `PB` | 下层气压面的 U 风辅助场 | 对 `ua` 三维场按压力插值到默认 850 hPa。 |
| `_ua_upper` | m s-1 | `U`, `P`, `PB` | 上层气压面的 U 风辅助场 | 对 `ua` 三维场按压力插值到默认 200 hPa。 |
| `_va_lower` | m s-1 | `V`, `P`, `PB` | 下层气压面的 V 风辅助场 | 对 `va` 三维场按压力插值到默认 850 hPa。 |
| `_va_upper` | m s-1 | `V`, `P`, `PB` | 上层气压面的 V 风辅助场 | 对 `va` 三维场按压力插值到默认 200 hPa。 |
| `bl_mass_convergence` | kg m-2 s-1 | `mass_convergence`（100 m）、`mass_convergence`（200 m）、`mass_convergence`（300 m）、`mass_convergence`（400 m）、`mass_convergence`（500 m）、`mass_convergence`（600 m）、`mass_convergence`（700 m）、`mass_convergence`（800 m）、`mass_convergence`（900 m）、`mass_convergence`（1000 m） | 边界层整层水平质量通量辐合 | 将各指定高度的 `mass_convergence` 以高度为坐标做梯形积分；默认 100–1000 m。 |
| `center` | grid index | `slp_center` | `slp_center` 的兼容名称：海平面气压场中的风暴中心格点索引 `[y,x]` | 读取 `slp_center` 并以 `center` 保存；数值不另算。 |
| `center_geo` | degree | `slp_center`、`XLAT`、`XLONG` | 风暴 SLP 中心的经纬度坐标 | 以 `slp_center` 格点索引，从同一时次 `XLAT`、`XLONG` 提取 `[纬度,经度]`。 |
| `cloud_classification` | 1 | `classification` | `classification` 的兼容名称：基于回波和垂直速度的云/降水类型，类别值 0–5 | 读取 `classification` 并以 `cloud_classification` 保存；数值不另算。 |
| `diabatic_heating_polar` | K s-1 | `diabatic_heating`、`slp_center` | 以 `slp_center` 为原点表示的 **微物理潜热加热率**，并非所有物理过程的总非绝热加热率（极坐标场） | 读取 `diabatic_heating` 与中心，按实际网格间距双线性插值为 360 个方位角 × 半径坐标；半径受边界约束。 |
| `height_center` | grid index | `layer_center` | `layer_center` 的兼容名称：每个请求高度的气压中心格点索引 `[y,x]` | 读取 `layer_center` 并以 `height_center` 保存；数值不另算。 |
| `land_boundary` | degree | `XLAND`、`XLAT`、`XLONG` | 参考域的海岸线点集 | 在 `XLAND` 相邻列陆海类别不同处提取两侧格点的 `XLAT`、`XLONG`。 |
| `layer_center` | grid index | `pressure` | 每个请求高度的气压中心格点索引 `[y,x]` | 默认在该高度 `pressure` 场上独立运行 Bezier/PCC，不读取上一层中心；仅显式 `layer_mode="vertical"` 时由规划器加入地面或前一高度中心约束。 |
| `pressure_center` | grid index | `layer_center` | `layer_center` 的兼容名称：每个请求高度的气压中心格点索引 `[y,x]` | 读取 `layer_center` 并以 `pressure_center` 保存；数值不另算。 |
| `rmw_by_height` | km | `ua`、`va`、`slp_center` | 各高度相对 SLP 中心的最大方位平均切向风半径 | 在当前高度由 `ua`、`va` 求切向风，围绕 `slp_center` 求方位平均并取峰值半径。 |
| `rmw_by_layer_center` | km | `ua`、`va`、`layer_center` | 各高度相对该高度气压中心的最大方位平均切向风半径 | 在当前高度由 `ua`、`va` 求切向风，围绕当前高度的 `layer_center` 求方位平均并取峰值半径。 |
| `slp_center` | grid index | `slp` | 海平面气压场中的风暴中心格点索引 `[y,x]` | 默认对 `slp` 场运行 `bezier_pcc_v1`；d03 首猜搜索半径 60 km、PCC 半径 44 格，不做时间平滑。 |
| `storm_motion` | mixed | 请求时间序列各时次的 `center_geo` | 风暴中心平移速度和方向向量 | 用相邻请求时次 `center_geo` 的经纬度差计算 U、V 平移速度、速度大小和方向。 |
| `theta` | K | `T` | 完整位温 | `getvar("theta")` 将 WRF 扰动位温 `T` 加 300 K，再按高度插值。 |
| `theta1_polar` | K | `theta`、`layer_center` | 以 逐高度 `layer_center` 为原点表示的 完整位温（极坐标场） | 读取 `theta` 与中心，按实际网格间距双线性插值为 360 个方位角 × 半径坐标；半径受边界约束。 |
| `theta_anomaly` | K | `theta`、`theta1_polar`（d02） | 位温相对环境的异常 | 目标域 `theta` 减去环境域同高度 `theta1_polar` 在指定环带的半径加权均值。 |
| `theta_anomaly1_polar` | K | `theta_anomaly`、`layer_center` | 以 逐高度 `layer_center` 为原点表示的 位温相对环境的异常（极坐标场） | 读取 `theta_anomaly` 与中心，按实际网格间距双线性插值为 360 个方位角 × 半径坐标；半径受边界约束。 |
| `u` | m s-1 | `U` | 模型网格 U 风的 v3 别名 | 与 `ua` 一样通过 `getvar("ua")` 去交错并按高度插值。 |
| `v` | m s-1 | `V` | 模型网格 V 风的 v3 别名 | 与 `va` 一样通过 `getvar("va")` 去交错并按高度插值。 |
| `vt1` | m s-1 | `ua`、`va`、`layer_center` | 相对逐高度气压中心的切向风 | 把 `ua`、`va` 投影到以当前高度 `layer_center` 为原点的逆时针切向方向。 |
| `XLAND` | 1 | `XLAND` | 陆海掩膜 | 直接读取 `XLAND`；本项目中陆地为 1、海洋为 2。 |
| `XLAT` | degree_north | `XLAT` | 网格纬度 | 直接读取同一时次的二维 `XLAT`。 |
| `XLONG` | degree_east | `XLONG` | 网格经度 | 直接读取同一时次的二维 `XLONG`。 |

## 使用时需注意

- `dbz_asymmetry`、`mdbz_asymmetry`、`precipitation_asymmetry` 默认积分半径为 200 km；当前入口对 d03 的极坐标输出半径为 100 km。需要统一这两个配置，不能把默认 200 km 指数直接套在 100 km 场上。
- `theta_e_anomaly` 的环境域默认 d02，环境环带默认 400–500 km；当前入口按域设置的极坐标半径为 d02 800 km、d03 100 km。
- `shear_200_850_*` 默认使用 d02 的 200/850 hPa 风场和同一时次的 d02 `slp_center`。按 d02 风场元数据中的 `DX/DY` 换算物理距离，先做双线性极坐标插值，再在 150–350 km（含端点）的 360 个方位、各径向采样点上取算术均值；默认径向步长为 `min(DX,DY)`，因此 d02 的 5 km 网格使用 5 km 步长。这是等方位、等半径采样均值，不是面积加权均值。
- 对 d03 提出风切变请求时，输出仍保存在 d03，输入依赖来自 d02。可通过 `shear_source_domain`、`shear_radius_inner_km`、`shear_radius_outer_km` 修改源域及半径；旧的以网格数为单位的 `shear_radius_in/out/max` 参数不再控制此算法。环带外缘必须落在源域中心到各边界的安全半径内。
- 2026-10-27 起默认 `layer_center` 逐高度独立定位，所有 `*1_polar` 使用对应高度的中心；只有显式 `layer_mode="vertical"` 才依赖逐高度连续中心链。旧缓存不会自动转换，使用前应核对算法元数据；无论哪种方法，科学解释前均应检查中心轨迹。
- `pvo` 的 v3 计算把地图比例因子固定为 1；直接 `wrf.getvar("pvo")` 会使用 WRF 的 `MAPFAC_U/V/M`，两种口径的数值不保证完全相同。
- 当前 [`process_data.py`](process_data.py) 的 `VARIABLES` 最终值只请求三个变量；本表说明注册表支持的变量，不表示这些变量目前都已计算或保存。

## 来源

- 项目实现：[`wrf_processing_v3/_scientific_registry.py`](wrf_processing_v3/_scientific_registry.py)、[`wrf_processing_v3/register.py`](wrf_processing_v3/register.py)、[`wrf_processing_v3/wrf_reader.py`](wrf_processing_v3/wrf_reader.py)、[`wrf_processing_v3/variables/`](wrf_processing_v3/variables/)、[`process_data.py`](process_data.py)。
- WRF 原始输出变量说明：[WRF Output Variables](https://www2.mmm.ucar.edu/wrf/site/users_guide/output_variables.html)；微物理潜热加热字段：[WRF Runtime I/O](https://www2.mmm.ucar.edu/wrf/wrf_tutorial/runtime_io.html)。
- `wrf-python` 诊断名称、单位与产品：[wrf.getvar 文档](https://wrf-python.readthedocs.io/en/main/user_api/generated/wrf.getvar.html)；具体输入字段与计算顺序也按本环境的 `wrf-python` 1.4.2 源码核对。

### 等高面相对涡度（2026-10-27）

| 变量 | 单位 | 依赖 | 物理含义 | 计算方式 |
|---|---|---|---|---|
| `vort_rel` | s-1 | `ua`、`va` | 等高面上垂直相对涡度，正值表示气旋性旋转 | ζ=∂va/∂x−∂ua/∂y，使用等高面已插值的模型网格风和实际 dx/dy（米）；内部中心差分、边界二阶单边差分。不加 f，不是位涡，也不是原始交错模式层上的涡度。平滑仅用于图件显示，不属于该变量算法。 |
