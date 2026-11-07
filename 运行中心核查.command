#!/bin/bash
# Claude 2026-11-02：只读核查 processed_v3 里“依赖中心”的切片用的中心参数（不改数据、不处理 wrfout）。
# 报告写到 .runtime/中心核查/summary.txt 与 audit.json。
cd "$(dirname "$0")" || exit 1
PY=/opt/anaconda3/envs/wrf-webvis/bin/python
export PYTHONPATH="$PWD:$PWD/wrf_webvis:$PWD/处理数据:$PWD/处理数据_v3"
mkdir -p .runtime/中心核查
{
  echo "start $(date)"
  "$PY" server_tools/center_audit.py "$@"
  echo "EXIT_AUDIT $?"
  echo "end $(date)"
} 2>&1 | tee .runtime/中心核查/latest.log
