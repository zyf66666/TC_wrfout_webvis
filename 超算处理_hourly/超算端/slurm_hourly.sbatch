#!/bin/bash
# Slurm 作业模板。在后处理目录下提交，分区名写在命令行（sinfo 查看可用分区）：
#   sbatch -p <分区名> tc_hourly_sc/slurm_hourly.sbatch WRF_0_sea_P1
# 多个试验并行（不同试验写不同文件，互不干扰），每个试验一个作业：
#   for e in WRF_0_sea_P1 WRF_0_sea_P2; do sbatch -p <分区名> -J tc_$e tc_hourly_sc/slurm_hourly.sbatch $e; done
# 先只看计划：sbatch -p <分区名> tc_hourly_sc/slurm_hourly.sbatch WRF_0_sea_P1 --dry-run
#SBATCH -J tc_hourly
#SBATCH -N 1
#SBATCH -n 1
#SBATCH -c 2
#SBATCH --mem=8G
#SBATCH -t 04:00:00
#SBATCH -o tc_hourly_%j.out

cd "${SLURM_SUBMIT_DIR:-.}"
# 作业里不一定会读 ~/.bashrc，conda 命令可能找不到：这里主动加载一次
if ! command -v conda >/dev/null 2>&1 && [ -f "$HOME/.bashrc" ]; then
    source "$HOME/.bashrc"
fi
echo "节点 $(hostname)，目录 $(pwd)，开始 $(date)"
bash tc_hourly_sc/run_hourly.sh "$@"
status=$?
echo "结束 $(date)，退出码 $status"
exit $status
