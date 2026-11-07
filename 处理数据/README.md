# 数据处理说明

主要入口是 `process_data.py`。用户只需要修改文件顶部的参数，不需要启动 FastAPI 或浏览器。

主要参数：

- `EXPERIMENTS`：试验名称列表。
- `EXPERIMENTS_ROOT` / `WRFOUT_TEMPLATE`：每个试验的 `wrfout` 目录。
- `PROCESSED_ROOT`：处理结果根目录。
- `DOMAINS`：需要处理的 domain。
- `TIMES`、`HEIGHTS`：时间和高度列表，可直接使用 `range`。
- `VARIABLES`：普通变量、中心、极坐标变量和诊断量。
- `FORCE`：是否覆盖已有文件。
- `DRY_RUN`：只列出处理计划，不写数据。
- `OPTIONS`：时次映射、中心、极坐标、风切变和涡旋倾斜参数。

运行：

```bash
cd "/Users/zhuangyifan/Documents/热带气旋研究/wrf-webvis"
conda activate wrf-webvis
python 处理数据/process_data.py
```

`FORCE=False` 时，目标文件存在就直接复用，不比较缓存 metadata 是否仍然有效。

## 极坐标中心的选择

- `rh_polar`：使用海平面气压中心，将各高度的 `rh` 插值到极坐标。
- `rh1_polar`：使用每个目标高度的气压中心，将该高度的 `rh` 插值到极坐标。

这里的 `1` 表示选择各层中心，原始数据仍读取 `rh`，不需要创建 `rh1`。
`dbz1_polar`、`theta_e1_polar`、`wa1_polar`、`ua1_polar`、`pvo1_polar` 等遵循相同规则。
`vr1_polar`、`vt1_polar` 则使用根据各层中心计算的 `vr1`、`vt1` 径向风和切向风。
基础变量或所选高度的中心缺失时，处理流程会自动补齐；已有缓存按 `FORCE` 设置复用。

例如，同时生成两种中心的相对湿度极坐标数据：

```python
VARIABLES = ["rh_polar", "rh1_polar"]
HEIGHTS = [500, 1000, 2000]
```

## 按各层中心插值的 300 km 安全时段

`process_data.py` 当前默认配置为 16 个试验、d03、100–10000 m 共 46 层、半径 300 km，变量只启用 `rh1_polar`。在项目根目录运行：

```bash
conda run --no-capture-output -n wrf-webvis python 处理数据/process_data.py
```

将 `VARIABLES` 改为 `["pvo1_polar"]` 即可计算位涡的各层中心极坐标结果；也可以同时填写 `["rh1_polar", "pvo1_polar", "theta_e1_polar"]`。安全筛选适用于注册的三维变量按层中心直接插值到极坐标的形式，包括 `dbz1_polar`、`wa1_polar`、`ua1_polar`、`vr1_polar`、`vt1_polar` 等。

入口会补齐每个变量缺少的源场和层中心，再用各个时次、各个高度的实际源网格尺寸检查边界。`pvo1_polar` 使用 `pvo`，无需 RH 缓存；`vr1_polar`、`vt1_polar` 使用 `vr1`、`vt1`。任一层不满足 300 km，整个时次都会跳过；不会缩小半径或外推。运行不依赖 `.runtime` 下的临时脚本、暂存文件或本次报告里的固定上限。

原先 RH 专用的开关已统一命名为 `LAYER_POLAR_BOUNDARY_POLICY` 和 `LAYER_POLAR_AUDIT_ONLY`，供所有上述变量使用。用户参数区有以下开关：

- `LAYER_POLAR_BOUNDARY_POLICY = "all_safe"`：默认，计算全部安全时次，包括越界后恢复可用的时次。
- `LAYER_POLAR_BOUNDARY_POLICY = "continuous"`：只计算从首个请求时次起、首次越界之前的连续安全段。
- `LAYER_POLAR_BOUNDARY_POLICY = "off"`：恢复原有行为，不自动筛选。
- `LAYER_POLAR_AUDIT_ONLY = True`：只读取对应变量的现有源场和中心，重新生成报告，不计算变量；缺少输入会报错。启用安全筛选时，`DRY_RUN=True` 也执行这项只读检查（报告仍会写出）。
- `FORCE=False`：按项目现有缓存规则复用已存在的极坐标文件，补算缺失部分。要重算全部所选极坐标文件，设置 `FORCE=True`；安全模式会使用已经准备并检查过的源场和中心，避免插值阶段再次改动输入。

修改半径或中心算法参数后，请设置 `FORCE=True` 重算极坐标缓存；`FORCE=False` 沿用项目“文件存在即复用”的约定。

每个试验、每个变量分别写报告到 `processed/<member>/d03/<variable>_safety.json`（例如 `pvo1_polar_safety.json`），包含逐层安全半径、每时次的限制高度、连续上限、全部安全/越界时次以及实际选中的时次。控制台也会打印摘要。混合请求中的各层中心极坐标变量分别筛选；`pvo_polar` 等 SLP 中心变量和普通变量仍按原始时段处理。网格、中心、高度和半径相同时，不同变量会得到相同安全时次；如果实际源网格不同，则按各自边界判定。

2026-09-06 的当前缓存检查结果如下，均包含结束时次。未提前越界的试验，其上限就是当前模拟的最后输出时次。

| 方位 | 陆地连续上限 (h) | 海洋连续上限 (h) |
|---|---:|---:|
| 0° | 120 | 120 |
| 45° | 120 | 120 |
| 90° | 75 | 120 |
| 135° | 41 | 69 |
| 180° | 32 | 59 |
| 225° | 39 | 57 |
| 270° | 61 | 84 |
| 315° | 102 | 102 |

越界后恢复的安全时次：90° 陆地 77–78 h，135° 陆地 43 h，180° 陆地 37–39 h，225° 海洋 59–69 h。默认 `all_safe` 会包括这些时次。修改高度、半径、时间或中心后，以上静态表不能替代入口的新检查。

本轮默认关闭额外的网格/路径坐标生成；需要时重新设置 `GRID_COORDINATE_MEMBERS`、`TRACK_CENTER_MEMBERS`。`CLEAN_OLD_METADATA=False` 保留现有 metadata；仅显式进行旧布局迁移时才开启它。

## 位涡 PV

`VARIABLES = ["pvo"]` 计算并保存各个 `HEIGHTS` 的位涡，单位为 **PVU**。
`VARIABLES = ["pvo_polar"]` 会先补齐 `pvo` 和 SLP 中心，再生成极坐标缓存；
也可同时填写 `["pvo", "pvo_polar"]`。绘图变量填写 `pvo`，色标标签可填写 `PVU`，缩放系数保持 1。

计算逻辑来自 `calc_pv.py`，封装在 `wrf_processing/potential_vorticity.py`：

- 使用当前时次的原始交错网格 `U/V`、位温 `T + 300 K`、全气压 `P + PB`（Pa）和 `DX/DY`（m）。
- 保留本项目理想试验的地图因子恒为 1 的设定，不读取缺失的 `MAPFAC_U/V`。
- 优先使用当前 `wrfout` 的 `F`。仅当其缺失时，读取该文件同目录的 `wrfinput_d01` 的首个时次 `F`，确认是常数后扩展到当前网格。常数判断容差为 `rtol=1e-6, atol=1e-12`。
- 备用文件不在同目录时，设置 `OPTIONS["pvo_wrfinput_d01"]`；相对路径相对于当前 `wrfout` 所在目录，绝对路径须对应当前试验。
- 每个时次先在原始模式层计算一次 `wrf.pvo`，再插值到全部目标高度；返回值已是 PVU，不再乘 `10^6`，也不做风暴移动速度订正。
- 原始输入含缺测值时报错；高度插值产生的缺测值保留为 NaN。输出 metadata 记录单位、地图因子设定和 `F` 来源。

输出沿用现有目录格式：

```text
processed/<member>/d03/pvo/<time>h/<height>m.npy
processed/<member>/d03/pvo_polar/<time>h/<height>m.npy
```

新增变量不会自动更新已有缓存。若需要重新计算已存在的 `pvo`（例如更换备用 `F` 文件），设置 `FORCE=True`，并同时重算相应的极坐标缓存。
