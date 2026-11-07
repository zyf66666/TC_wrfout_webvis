# processed_v3 NetCDF4/HDF5 pipeline

This directory is isolated from `处理数据`, `处理数据_v2`, and their existing
outputs. The default v3 destination is `/Volumes/land_1/processed_v3`.
All runtime modules, variable handlers, and registry definitions required by
`process_data.py` are bundled here; neither old processing package needs to be
on `PYTHONPATH`.

> 新增一个要计算的变量：见 [`新增变量流程.md`](新增变量流程.md)（2026-10-21）。网页上添加新试验：数据管理页“＋ 添加试验”，登记写在 `wrf_webvis/storage_data/custom_experiments.json`，不改 `project_config.py`。

## 当前批处理配置与网页入口

`project_config.py` 保存命令行与网页共用的 wrfout 目录映射和默认科学参数。
命令行入口 `process_data.py` 当前选择 14 个实验、d03、各实验配置的结束时次、
100–15000 m 的标准高度，以及 `VARIABLES` 中的基础变量；依赖由 v3 自动规划。
命令行默认 dry-run，只有 `--run` 才会写结果。

网页“数据管理”可从目录映射中逐个选择实验、domain、变量、时次和高度，
预览依赖后提交后台任务；同一任务中不混合多个实验。历史任务和中断状态保存在 SQLite。
默认输出仍在 `/Volumes/land_1/processed_v3`，改变科学参数后的配置写在其 `profiles/` 子目录。

The optional `rmw_by_height.nc` product stores one RMW in kilometres per time and requested height.
Each level uses `ua`/`va` interpolated to that height and the same low-level
`slp_center`; the slice metadata includes the peak azimuthal-mean tangential
wind in m s-1. The existing `rmw` diagnostic remains a lowest-model-level
value. No weak-vortex or incomplete-ring filter is applied to the new product.

`rmw_by_layer_center.nc` uses the `layer_center` calculated at each height
along the vertically continuous center chain, with the same wind inputs and
output layout. When setting just one variable, keep the trailing comma:
`VARIABLES = ("rmw_by_height",)`.

```bash
conda run -n wrf-webvis python 处理数据_v3/process_data.py
conda run -n wrf-webvis python 处理数据_v3/process_data.py --run
```

The command prints progress for source indexing, dependency planning/cache
inspection, and execution. Planning opens each variable file once and checks
the requested `completed` slices by coordinate.

Force one variable or one exact slice without forcing every dependency:

```bash
conda run -n wrf-webvis python 处理数据_v3/process_data.py --run \
  --force-variable rmw_by_layer_center
conda run -n wrf-webvis python 处理数据_v3/process_data.py --run \
  --force-slice rmw_by_layer_center,60,1000,3
```

## 处理套餐、数据集 post、内核区风切变（2026-11-02）

- `processing_presets.json`：套餐 → 若干组，每组 `variables`、`time_step_minutes`、`heights`（`null` / `"standard"` / `"warm_core"` / 整数列表）、`enabled`。由 `wrf_processing_v3/processing_presets.py` 读取并校验（变量须已登记，三维变量所在组须有高度）。网页“数据管理 → 处理套餐”和命令行共用。
- `project_config.POST_ROOT = /Volumes/land_1/post`、`DATASETS = {"post": POST_ROOT, "v3": OUTPUT_ROOT}`。post 只放用户在网页上亲自选择处理的数据，与 processed_v3 互不复用。`OUTPUT_ROOT` 与命令行默认不变。
- 内核区风切变 `shear_core_200_850_{u,v,magnitude,direction}`：目标域（套餐用 d03）`slp_center` 为圆心、150–350 km 环带、200–850 hPa，与环境切变 `shear_200_850_*`（d02）同一算法。半径可用 `core_shear_radius_inner_km` / `core_shear_radius_outer_km` 覆盖。

```bash
conda run -n wrf-webvis python 处理数据_v3/process_data.py --preset basic_tc --dataset post            # dry-run
conda run -n wrf-webvis python 处理数据_v3/process_data.py --preset basic_tc --dataset post --run      # 实际计算（须用户同意）
conda run -n wrf-webvis python 处理数据_v3/process_data.py --preset basic_tc --preset-group shear_1h --dataset post
```

每组的时次为 0 到该试验结束时次（`EXPERIMENT_END_HOURS`），按组的步长取。

风暴相对风（2026-11-03）：后缀 `_sr` = 用风暴相对风算，`_sr1` = 以 layer_center 分解。变量表 `_scientific_registry.STORM_RELATIVE_VARIABLES`（`ua_sr`、`va_sr`、`vr_sr`、`vt_sr`、`vr_sr1`、`vt_sr1`、`inflow_angle_sr(1)`、`qflux_r_sr(1)`），另有 `rmw_by_height_sr`、`rmw_by_layer_center_sr`（`STORM_RELATIVE_ALL`）与 `vr_sr(1)_ventilation_polar`；算法见 `variables/_storm_relative.py`；`motion_correct` 打开时自动改读 `_ua_earth`/`_va_earth`；`_scientific_registry.LAYER_CENTER_WINDS` 让 `vr_sr1_polar` 等以自身为源、以 layer_center 为原点。套餐 `merge_groups: false` 时各组不合并（`analysis_tc`）。

处理加速（2026-11-03 下午）：`wrf_reader.fill_exact_levels` 修正 interplevel 在“层高度恰等于目标高度”的格点返回缺测的问题（取该层原值，元数据 `interp_exact_level_fix: v1`），真正缺测才走 `common.fill_nan`；`NetCDFStorage.save_many` 把同一文件的多个切片一次写入（读取器按字段每批 16 个，`wrf_reader.SAVE_BATCH`），`save` 也走同一路径，只 sync 一次；同一批高度一次 `interplevel`（`interpolate_many`，与逐层逐值相同）。

完成情况索引（`wrf_processing_v3/completion_index.py`，2026-11-03）：`<变量>.nc.index.json` 记录坐标、完成标记和每个已完成切片的科学参数（去重），带 .nc 的 [大小, mtime_ns, ctime_ns, inode] 签名；签名不符即失效。`NetCDFStorage.save` 持写锁时增量更新；`existing()`、`exists()`、`check_cache_compatibility` 先用索引，没有则从 .nc 整体读一次并写出索引。只是加速副本，不改 .nc 与数值；`WRF_PROCESSING_V3_COMPLETION_INDEX=0` 关闭。

读取加速（`wrf_reader.py`，2026-11-02，结果逐值不变，`options["fast_source_reads"] = False` 可关闭）：只需要最低模式层的 ua/va 时只读 U/V 最低层再按 `wrf.destagger` 同一算式去交错；同一时次同时要 dbz 与 mdbz 时 mdbz 取 dbz 的柱最大值（wrf-python 的 mdbz 定义即如此）；同一时次先读 pressure，气压面风场复用它。快速读取写入的切片元数据带 `fast_read` 字段。核对脚本：`server_tools/check_fast_reads.py`（只读一个真实时次）。

## 1–6 km 涡旋倾斜

固定高度的新变量可与现有默认 2–8 km 的 `tilt_*` 同时保存：

| 变量 | 含义 | 单位 |
| --- | --- | --- |
| `tilt_1_6` | 6 km 中心相对 1 km 中心的水平距离 | km |
| `tilt_x_1_6` | 同一位移的东西分量 | km |
| `tilt_y_1_6` | 同一位移的南北分量 | km |
| `tilt_angle_1_6` | 同一位移的方向角，沿用 `-atan2(y, x)` 约定 | degree |

无需修改 `tilt_lower_height_m` 或 `tilt_upper_height_m`。新变量始终使用
1000 m 和 6000 m 的 `layer_center`；已有中心会复用，缺失时自动补算至
6000 m 的连续中心链。每个变量按时次保存在
`OUTPUT_ROOT/<实验>/d03/<变量名>.nc`，网页数据管理及时间曲线均可选择。

`--variable` 可重复指定，覆盖本次请求的 `VARIABLES`，不修改配置文件。
先预览，再执行以下命令（试验、domain 和时次仍由 `project_config.py` 决定）：

```bash
conda run -n wrf-webvis python 处理数据_v3/process_data.py --dry-run \
  --variable tilt_1_6 --variable tilt_x_1_6 \
  --variable tilt_y_1_6 --variable tilt_angle_1_6
conda run -n wrf-webvis python 处理数据_v3/process_data.py --run \
  --variable tilt_1_6 --variable tilt_x_1_6 \
  --variable tilt_y_1_6 --variable tilt_angle_1_6
```

已有新变量的完整切片默认跳过。需要再次计算时，可在上述运行命令中添加
对应的 `--force-variable`，例如 `--force-variable tilt_1_6`；要刷新四个量，
分别添加四次。原有 `tilt_*` 不会因这些命令被覆盖。

Sparse output-height requests still expand the `layer_center` dependency over
the standard height chain through the highest requested level. This preserves
the v2 vertically continuous-center result while writing target-variable data
only at the requested heights.

## Memory behaviour

A large `.nc` file is not loaded as one array. Readers locate one
`time_minutes`/`height_m` coordinate and read only that 2-D chunked slice. With
d03 805 x 805 `float32`, a raw Cartesian slice is about 2.5 MiB; a 360 x 101
polar slice is about 0.14 MiB. Plot reducers process height files sequentially.
The logical file may be tens of GiB without requiring that much RAM.

## Migration and comparison

Migration requires explicit coordinates and never edits the NPY source:

```bash
conda run -n wrf-webvis python 处理数据_v3/migrate_npy.py \
  --experiment WRF_315_land --domain 3 --variable theta \
  --time-minute 0 --height-m 1000 \
  --time-origin 2023-09-01_00:00:00
```

Run the fixed pilot comparison (shape, dtype, values, NaNs, units, coordinates,
and polar radius):

```bash
conda run -n wrf-webvis python 处理数据_v3/compare_v2_v3.py \
  --report 处理数据_v3/pilot_comparison.json
```

网页默认绘图根目录为 `/Volumes/land_1/processed_v3`，可在数据管理页选择参数配置并带入绘图表单。
网页绘图只读 v3 根目录或其参数配置目录中的已完成切片（2026-11-01 起完全去掉了 v2 回退；旧请求里的 processed_v2 根目录自动改指同级 processed_v3）。

See [SCHEMA.md](SCHEMA.md) for the on-disk contract.
