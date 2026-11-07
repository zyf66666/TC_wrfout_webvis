# 超算处理_hourly：在超算上逐小时算强度与中心，下载回本地画图

2026-11-02 Claude。用于超算上跑的新试验（如 `WRF_0_sea_P1`）：超算上算 1 h 分辨率的轴平均最大风速、局地最大风速、
最低海平面气压、中心（位置与经纬度），只把几 MB 的结果下载回来，本地画图、与已有试验对比。

| 文件 | 作用 |
|---|---|
| `tc_hourly_sc.tar.gz` | **上传到超算的包**（由 `打包.sh` 生成；含 `超算端/` 脚本 + 当时 `../处理数据_v3` 管线的原样拷贝） |
| `超算端/` | 超算上运行的脚本源码：`run_hourly.sh`（入口）、`run_hourly.py`、`export_hourly.py`、`check_env.py`、`v3_table.py`、`slurm_hourly.sbatch`、`README.md`（超算上的完整说明） |
| `本地/plot_hourly.py` | 本地画图：2×2（轴平均最大风、局地最大风、最低气压、路径），可叠加 processed_v3 里的对照组 |
| `打包.sh` | 本地管线改过后重新打包：`bash 打包.sh` |

## 流程

1. **上传**：把 `tc_hourly_sc.tar.gz` 传到超算的后处理目录（`./WRF_0_sea_P1/run` 所在那层）。
2. **超算上**：
   ```bash
   cd <后处理目录>
   tar xzf tc_hourly_sc.tar.gz
   conda activate py310
   bash tc_hourly_sc/run_hourly.sh WRF_0_sea_P1 --dry-run     # 看时次范围
   nohup bash tc_hourly_sc/run_hourly.sh WRF_0_sea_P1 > tc_P1.log 2>&1 &
   tail -f tc_P1.log
   ```
3. **下载** `tc_hourly_out/download/tc_hourly_WRF_0_sea_P1.tar.gz`（几 MB）。
4. **本地画图**（wrf-webvis 环境，不用解压）：
   ```bash
   conda activate wrf-webvis
   cd ~/Documents/热带气旋研究/wrf-webvis/超算处理_hourly/本地
   python plot_hourly.py ~/Downloads/tc_hourly_WRF_0_sea_P1.tar.gz \
       --control /Volumes/land_1/processed_v3/WRF_0_sea --out ~/Downloads/WRF_0_sea_P1_强度.png
   ```
   多个成员：把多个 `.tar.gz` 依次写在前面（最多 6 个）；`--hours 0 96` 限定时段；`--csv` 把对照组读出的数据也存成 CSV。

## 口径（与本地已有数据一致）

- 算法：`处理数据_v3` 原样（中心方案 base = Bezier PCC v1，d03 60 km 首猜搜索、逐时独立定位，不做时间平滑）。
- 风：最低模式层 ua/va（不是 10 m 风），与 `axis_mean_max_wind`、`local_max_wind` 相同。
- 最低气压：d03 全场 SLP 最小值（导出时由 slp.nc 现算）；表里另有中心格点 SLP 和中心 5 km 平均 SLP。
- 下载包里的 `d03/*.nc` 是 processed_v3 格式，可以放进 `/Volumes/land_1/processed_v3/<试验>/d03/`（或 post），
  在网页“数据管理 → ＋ 添加试验”登记后，网页时间曲线也能直接画。

## 测试

2026-11-02 在 Claude 的云端环境用合成涡旋（已知中心、强度、最低气压）跑通了 自检 → dry-run → 计算 → 断点复用 → 导出 → 画图，
中心、最低气压逐时与设定值一致。云端没有真实 wrf-python / netCDF4，这两处用了替身，所以第一次在超算上运行时请看一眼
`--dry-run` 和日志里的峰值是否合理。
