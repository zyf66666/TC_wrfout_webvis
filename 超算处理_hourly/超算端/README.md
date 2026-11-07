# tc_hourly_sc：超算上逐小时处理台风强度与中心

把 `./<试验>/run/wrfout_d03*` 处理成 1 h 分辨率的：轴平均最大风速、局地最大风速、最低海平面气压、中心位置，
并打成一个几 MB 的下载包。算法就是本地 `wrf-webvis/处理数据_v3` 的原样拷贝（`pipeline/`，版本见 `VERSION.txt`），
输出也是同一种 processed_v3 NetCDF 格式，所以和本地已有试验可以直接比较。

## 1. 上传、解压

把 `tc_hourly_sc.tar.gz` 传到**后处理目录**（`WRF_0_sea_P1/` 所在的那一层），然后：

```bash
cd <后处理目录>
tar xzf tc_hourly_sc.tar.gz          # 得到 tc_hourly_sc/
```

## 2. 运行

> 试验目录不在当前目录、而在上一层时（如在 `expr_new/post` 里运行、数据在 `expr_new/WRF_0_sea_P1/run`），
> 所有命令后面加 `--root ..`。

```bash
conda activate py310
bash tc_hourly_sc/run_hourly.sh WRF_0_sea_P1 --dry-run   # 先看计划：时次范围、缺哪些整点
bash tc_hourly_sc/run_hourly.sh WRF_0_sea_P1             # 自检 → 计算 → 导出下载包
```

- 多个试验：`bash tc_hourly_sc/run_hourly.sh WRF_0_sea_P1 WRF_0_sea_P2 WRF_45_sea_P1`（依次处理，一个失败不影响其余）。
- 登录节点断线会中断：用 `nohup bash tc_hourly_sc/run_hourly.sh WRF_0_sea_P1 > tc_p1.log 2>&1 &`，或 `screen`/`tmux`，
  或提交 Slurm 作业 `sbatch -p <分区名> tc_hourly_sc/slurm_hourly.sbatch WRF_0_sea_P1`（`sinfo` 看分区，输出在 `tc_hourly_<作业号>.out`）。
- 中断后重新运行同一条命令即可：已完成的时次自动跳过（`completed` 标记），只补没算完的。
- **模式还在跑时也可以先处理**：最近 30 分钟内还在被修改的 wrfout（可能正在写入）会整份跳过，不会把写了一半的时次存成结果；
  模式跑完后再运行一次同一条命令，就只补新时次，下载包也会更新成完整的。`--settle-minutes` 可改这个时间（0 = 不跳过）。
- 常用参数：`--start-hour 0 --end-hour 72`、`--step 60`（分钟）、`--run-subdir run`、`--out tc_hourly_out`、
  `--include-slp`（下载包里也带全场 SLP，较大）。环境名不是 py310 时：`TC_CONDA_ENV=xxx bash ...`。

耗时参考：每个时次读一次三维场算 SLP，约几秒；120 h 逐时约 10–20 min/试验（视文件系统而定）。内存 < 4 GB。

## 3. 下载

```
tc_hourly_out/download/tc_hourly_WRF_0_sea_P1.tar.gz    ← 只下载这个
tc_hourly_out/v3/WRF_0_sea_P1/d03/                      ← 完整结果，含中间量 slp/ua/va/XLAT/XLONG（较大，留在超算）
tc_hourly_out/logs/                                     ← 运行日志
```

单独重新导出（比如加上 slp）：`python tc_hourly_sc/export_hourly.py WRF_0_sea_P1 --include-slp`

## 4. 表里的量（`<试验>_hourly.csv`）

| 列 | 含义 |
|---|---|
| `vmax_axis_mean_ms` | 轴平均最大风速：最低模式层切向风以 `slp_center` 为圆心做方位平均，取各半径最大值（= 本地 `axis_mean_max_wind`） |
| `vmax_local_ms` | 局地最大风速：最低模式层全场风速最大值（= `local_max_wind`） |
| `rmw_km` | 上面方位平均切向风最大值所在半径（= `rmw`） |
| `slp_min_hPa` / `slp_min_y,x` | d03 全场海平面气压最小值及其格点 |
| `slp_min_offset_km` | 最小气压格点到 `slp_center` 的距离（检查定中心是否合理） |
| `slp_at_center_hPa` | `slp_center` 格点上的 SLP |
| `slp_center_5km_hPa` | 中心 5 km 圆盘平均 SLP（= `center_slp_5km`） |
| `center_y,x` | `slp_center`：d03 网格下标，中心方案 base（Bezier PCC v1，60 km 首猜搜索） |
| `center_lat,lon` | `center_geo`：中心格点在当时移动嵌套网格上的经纬度 |

## 5. 自检不通过时

- 缺 `netCDF4` / `wrf-python`：`conda install -n py310 -c conda-forge netcdf4 wrf-python`（或 `pip install netCDF4 wrf-python`）。
- **超算不能联网**（离线安装）：在能联网的电脑上（Mac 也可以）下载 Linux 版安装包再传上去：
  `pip download wrf-python netCDF4 --python-version 3.10 --only-binary=:all: --platform manylinux2014_x86_64 -d wheels`，
  超算上 `pip install --no-index --find-links wheels wrf-python netCDF4`。PyPI 上找不到 wrf-python 的 Linux 包时，
  改用 conda-forge 的离线包（或问管理员超算上有没有装好 wrf-python 的环境）。
- `DX 不是 1 km`：管线对 d03 按 1 km 网格算半径，先不要算，联系 Claude 改参数。
- 输出目录不支持 flock（Lustre 常见）：脚本会自动跳过文件锁，只要**同一个试验不要同时开两个进程**。
