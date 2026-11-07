# 服务器离线 WRF 处理与强度绘图

本包由你自行上传和运行；不含 SSH、联网调用、遥测或定时任务。运行时不依赖原仓库、网页、v1/v2 处理目录。`wrf_processing_v3/` 是当前项目科学处理模块的完整快照。

## 1. 解压和准备环境

把 `wrf_server_tools.tar.gz` 上传到你选择的服务器目录，再运行：

```bash
tar -xzf wrf_server_tools.tar.gz
cd wrf_server_tools
```

需要 Python >=3.10、numpy<2、scipy、netCDF4=1.6.5、matplotlib、wrf-python 和 setuptools<82。旧版 wrf-python 使用 pkg_resources，setuptools 82 起已移除该模块；部分 netCDF4 1.7.x 构建的 Dataset 迭代接口与 wrf-python<1.4.1 不兼容。已有满足要求的环境可以直接使用；否则先创建环境：

```bash
conda env create -f environment.yml
conda run -n wrf-server-tools python -V
```

推荐通过 `conda run` 指定环境运行，避免服务器 shell 未初始化造成 `conda activate` 失败：

```bash
conda run --no-capture-output -n wrf-server-tools python run_server.py --dry-run
conda run --no-capture-output -n wrf-server-tools python run_server.py --run
```

`python -V` 应显示 3.10 或更高版本。如果环境不存在，先完成上面的创建命令。如果更习惯激活环境，在 Bash/Zsh 中仅为当前 shell 加载 conda（无需执行 `conda init` 修改启动文件）：

```bash
source "$(conda info --base)/etc/profile.d/conda.sh"
conda activate wrf-server-tools
python -V
```

确认激活成功后，后续各节的 `python ...` 命令可以直接运行。也可以始终在它们前面添加 `conda run --no-capture-output -n wrf-server-tools`。如果看到 `dataclass() got an unexpected keyword argument 'slots'`，说明实际调用的解释器不支持 Python 3.10 引入的 `slots` 参数；重新确认运行环境，而不是只删除这一参数。

依赖安装需要可用的软件源；服务器离线时，请使用已安装的环境，或由你按服务器规定准备离线 conda 包。处理和绘图本身无需联网。pip 备选方式为 `python -m pip install -r requirements.txt`，但编译 wrf-python 可能需要 Fortran 编译器。

如果使用此前的命令建立了名为 `wrf-process` 的环境，遇到 `No module named 'pkg_resources'`，在该环境补装兼容版本后重试（无需重建环境或重新上传代码）：

```bash
conda install -n wrf-process --override-channels -c conda-forge "setuptools<82"
conda run --no-capture-output -n wrf-process python -c "import pkg_resources; import wrf; print('wrf-python import OK')"
conda run --no-capture-output -n wrf-process python run_server.py --dry-run
```

环境名不同则替换 `-n` 后的名字。手动创建新环境的完整命令为：

```bash
conda create -n wrf-process --override-channels -c conda-forge \
  python=3.10 "numpy<2" "setuptools<82" scipy "netcdf4=1.6.5" matplotlib wrf-python
```

如果环境已建好，实际读取时出现 `Dataset is not picklable`，先停止当前失败任务，再固定 netCDF4 版本：

```bash
conda install -n wrf-process --override-channels -c conda-forge \
  "netcdf4=1.6.5" "numpy<2" "setuptools<82"
conda run -n wrf-process python -c "import netCDF4, wrf; print('netCDF4:', netCDF4.__version__); print('wrf-python:', wrf.__version__)"
conda run --no-capture-output -n wrf-process python run_server.py --run
```

这是 wrf-python 上游已确认的兼容问题（NCAR/wrf-python #290、#291，v1.4.1 修复）。固定依赖后重跑原命令即可；已有状态文件和结果可继续使用。本修复仅更新依赖约束与入口版本检查，不修改 v3 科学模块。

## 2. 配置试验

编辑 `server_config.py`，默认已配置：

```python
EXPERIMENTS = {
    "WRF_315_land_P1": "/home/qqli_group/qqli/data/lqq_zyf/expr/WRF_315_land_P1/run",
    # 后续添加试验时取消注释并填写实际目录：
    # "WRF_315_land_P2": "/home/qqli_group/qqli/data/lqq_zyf/expr/WRF_315_land_P2/run",
}
```

只处理 d03。默认每 60 分钟取一个实际存在的时次，不设旧试验的结束小时。层中心和 dbz 的请求高度为 100–1000 m 每 100 m、1250–10000 m 每 250 m（46 层）。高度沿用当前代码的 `wrf.getvar('z', units='m')` 插值口径，不增加 AGL 转换。`axis_mean_max_wind` 使用最低模式层，不受请求高度列表影响。

`OPTIONS` 已复制当前科学参数：地面中心使用平滑首猜 + 浮点 PCC v2；层中心使用按高度连续的 Bezier PCC v1。保存地面和层中心网格坐标，地面经纬度由当前时次的 XLAT/XLONG 映射，支持移动嵌套网格。

气压指标是 **中心周围 5 km 圆盘平均海平面气压**，不是最低气压。风速指标是 **最低模式层方位平均切向风剖面的最大值**。dbz 保存指定高度水平场；其余原始依赖也会自动保存。

## 3. 运行

```bash
# 检查依赖、源文件和依赖计划，默认不计算
python run_server.py --dry-run

# 增量计算，并更新服务器上的强度图
python run_server.py --run

# 后续新增 wrfout 后，重复运行即可
python run_server.py --run

# 只处理指定试验；--experiment 可重复指定
python run_server.py --run --experiment WRF_315_land_P1

# 试验已结束：允许处理稳定的最后一个 wrfout
python run_server.py --run --simulation-finished

# 只读已有标量结果绘图，不需要 wrfout 或 wrf-python
python run_server.py --plot-only

# 可在命令行指定其他结果目录
python run_server.py --run --output-root /your/absolute/path/processed_v3
```

默认排除修改时间最新的文件（并列最新也排除），其他文件需至少 120 秒未修改，再观察 2 秒确认文件大小和修改时间不变，并检查 NetCDF 时间与必要字段。默认目录只扫描 `run/wrfout_d03*`。多个时次写在同一文件时，按文件内 `Times` 逐条索引；最新文件内的所有时次均延后处理。若仍在运行且只有一个输出文件，本次可能没有可处理时次，等待下一个文件生成后再运行。

这些检查是保守的写入检测，不能替代 WRF 的文件关闭信号。`--simulation-finished` 仅在你确认 WRF 已停止写入后使用；文件仍需通过稳定检查。无法读取或缺少必要字段的文件会暂缓并打印原因。真实读取过程中源文件改变时，对应时次的结果会作废，后续运行重新计算。

每个试验固定保存 `server_state.json`，记录时间原点、科学参数、模块指纹和每个输入时次的文件指纹。默认遵循当前项目的 `START_DATE`；首次仅保留了 restart 后文件时，在 `TIME_ORIGINS` 中明确填写原始模拟开始时间。以后删除最早的 wrfout 也不会让横轴重新从 0 开始。

源文件被替换或改变后，相应时次重新计算。科学参数、模块版本、源目录或高度改变时，使用新的 `OUTPUT_ROOT`；入口会拒绝混用旧结果。未登记 `server_state.json` 的已有结果目录同样拒绝直接补算，可另建目录；`--plot-only` 仍能读取符合 v3 格式的旧标量结果。不要手动删除状态文件以绕过检查。

试验逐个、时次逐个运行；先完成全部强度时次，再处理层中心和 dbz。大体量处理失败时，已有强度可以独立绘图。单个时次/试验失败不会阻止其余项目；有失败时进程退出码为 1。中断后重复同一命令复用已完成切片，补齐中断部分。同一试验同时启动第二个计算进程会因锁失败退出。

## 4. 后台运行和日志

```bash
mkdir -p logs
nohup conda run --no-capture-output -n wrf-server-tools python -u run_server.py --run > logs/process.log 2>&1 &
echo $!
tail -f logs/process.log
```

该后台示例适用于服务器允许直接运行计算的情形。如果集群要求 Slurm/PBS，请把环境激活和 `python -u run_server.py --run` 放入你自己的提交脚本，按集群规则申请 CPU、内存和时限；本包不猜测队列或资源限制。

## 5. 输出位置

默认根目录：`/home/qqli_group/qqli/data/lqq_zyf/expr/processed_v3`。

```text
processed_v3/
  WRF_315_land_P1/
    server_state.json
    d03/
      slp_center.nc
      center_geo.nc
      layer_center.nc
      axis_mean_max_wind.nc
      center_slp_5km.nc
      dbz.nc
      ... 原始依赖及锁文件
    figures/
      intensity.png
      intensity.pdf
      intensity.csv
  figures/
    intensity_comparison.png   # 至少两个试验有可用强度时生成
    intensity_comparison.pdf
```

每个变量单独一个 NetCDF4 文件，按时次/高度压缩分块保存，`completed=1` 才可读取。NetCDF 的高度坐标预留到 15000 m，但本次仅计算配置中的高度；未计算高度保持 `completed=0`，不代表额外计算到了 15 km。

强度图是两行共享横轴的面板：上方风速 m/s，下方中心 5 km 平均气压 hPa，横轴为模拟小时。PNG 为 300 dpi，PDF 为矢量图。多试验对比横轴以各自固定原点对齐；可以有不同的绝对开始日期。图中缺失小时断线，CSV 对应值留空。CSV 包含时次、模拟小时、有效时间、风速、气压、地面中心网格坐标及经纬度。

## 6. 常见情况

- `检查失败：依赖 ... 无法导入`：确认激活了正确的环境，再按第 1 节安装缺少的模块。
- `No module named 'pkg_resources'`：在实际运行的环境安装 `setuptools<82`，参照第 1 节的修复命令。
- `Dataset is not picklable`：旧版 wrf-python 与部分新版 netCDF4 的迭代接口不兼容，参照第 1 节固定 `netcdf4=1.6.5` 后重跑。
- `CondaError: Run 'conda init' before 'conda activate'`：按第 1 节使用 `conda run`，或为当前 Bash/Zsh 加载 `conda.sh`。
- Python 版本提示小于 3.10：用 `conda run -n wrf-server-tools python -V` 检查目标环境；若现有同名环境仍是旧版，先在该环境安装满足要求的 Python 和依赖，再运行。
- `暂缓最新文件`：运行中的最新文件被保护，后续有新文件后重跑；确认模拟结束才加 `--simulation-finished`。
- `重复源时间`：run 目录有两个文件含同一时次，先核实并整理源目录，入口不会自行挑选其中一个。
- `科学参数 ... 已改变`：指定新的结果目录，保持新旧参数分开保存。
- `绘图失败`：确认至少风速或气压有已完成且有限的值，检查前面的计算日志；其他可用试验仍会生成图片。
- `QRAIN` 等字段缺失：核对 WRF 输出配置是否包含反射率及压力/风场诊断所需的变量。

服务器真实 wrfout 未在本地访问；交付前的验证使用合成 NetCDF 和模拟涡旋场。你首次运行可先检查 dry-run 列出的时间原点、时次、层数，再执行计算。
