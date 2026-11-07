#!/bin/bash
# 在后处理目录（./WRF_0_sea_P1/run 所在的目录）下运行：
#   bash tc_hourly_sc/run_hourly.sh WRF_0_sea_P1                # 自检 → 计算 → 导出下载包
#   bash tc_hourly_sc/run_hourly.sh WRF_0_sea_P1 WRF_0_sea_P2   # 多个试验依次处理
#   bash tc_hourly_sc/run_hourly.sh WRF_0_sea_P1 --dry-run      # 只看计划
# 其他参数原样传给 run_hourly.py（--start-hour / --end-hour / --step / --out / --run-subdir / --include-slp）。
# conda 环境名默认 py310，可用环境变量改：TC_CONDA_ENV=xxx bash tc_hourly_sc/run_hourly.sh ...
# 跳过自检：TC_SKIP_CHECK=1 bash tc_hourly_sc/run_hourly.sh ...

set -o pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_NAME="${TC_CONDA_ENV:-py310}"

if [ "${CONDA_DEFAULT_ENV:-}" != "$ENV_NAME" ]; then
    if command -v conda >/dev/null 2>&1; then
        eval "$(conda shell.bash hook)"
        conda activate "$ENV_NAME" || { echo "conda activate $ENV_NAME 失败"; exit 1; }
    else
        echo "找不到 conda 命令：请先 module load 对应的 anaconda/miniconda 并 conda activate $ENV_NAME 再运行"
        exit 1
    fi
fi
echo "Python: $(command -v python)  (conda 环境 ${CONDA_DEFAULT_ENV:-?})"

export HDF5_USE_FILE_LOCKING=FALSE
export OMP_NUM_THREADS=1

# 日志放在 --out 指定的目录（默认 tc_hourly_out）
OUT="tc_hourly_out"
args=("$@")
for ((i = 0; i < ${#args[@]}; i++)); do
    [ "${args[$i]}" = "--out" ] && OUT="${args[$((i + 1))]}"
done
mkdir -p "$OUT/logs"
# 日志名带试验名和作业号，几个作业同时跑不会写进同一个文件
LOG="$OUT/logs/run_${1:-x}_$(date +%Y%m%d_%H%M%S)_${SLURM_JOB_ID:-$$}.log"
echo "日志：$LOG"

{
    if [ "${TC_SKIP_CHECK:-0}" != "1" ]; then
        echo "===== 环境自检 ====="
        python -u "$HERE/check_env.py" "$@" || { echo "自检未通过，停止。（确认问题不影响时可 TC_SKIP_CHECK=1 跳过自检）"; exit 2; }
    fi
    echo
    echo "===== 计算 ====="
    python -u "$HERE/run_hourly.py" "$@"
} 2>&1 | tee "$LOG"
exit "${PIPESTATUS[0]}"
