#!/bin/bash
# Claude 2026-11-01：“去掉 v2 回退”改动用的全量测试。只跑测试、不重启网页。
# 结果写入 .runtime/去v2测试/<时间>.log，并复制一份为 latest.log（Claude 直接读它）。
cd "$(dirname "$0")" || exit 1
PY=/opt/anaconda3/envs/wrf-webvis/bin/python
DIR=.runtime/去v2测试
mkdir -p "$DIR"
LOG="$DIR/$(date +%Y%m%d_%H%M%S).log"
export PYTHONPATH="$PWD:$PWD/wrf_webvis:$PWD/处理数据:$PWD/处理数据_v3"
export MPLBACKEND=Agg
{
  echo "start $(date)"; "$PY" --version
  echo "git $(git rev-parse --short HEAD) 改动文件数 $(git status --short | wc -l)"
  echo "===== 1. Python 全部测试"
  "$PY" -m pytest -q -p no:cacheprovider --continue-on-collection-errors -rfE tests wrf_webvis/tests 处理数据_v3/tests
  echo "EXIT_PYTEST $?"
  echo "===== 2. 前端测试"
  node --test wrf_webvis/tests/*.mjs 2>&1 | tail -40
  echo "EXIT_NODE ${PIPESTATUS[0]}"
  echo "===== 3. wrf-lab 测试"
  echo "end $(date)"
} > "$LOG" 2>&1
cp "$LOG" "$DIR/latest.log"
echo "测试完成，结果在 $PWD/$LOG ，可以关闭此窗口。"
