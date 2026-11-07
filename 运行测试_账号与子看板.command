#!/bin/bash
# Claude 2026-11-06：账号登录与子看板改动的测试。用 conda wrf-webvis 环境跑，结果写入 .runtime/账号与子看板_测试日志.txt。
# 全部通过后再运行 ./restart_web.sh 重启网页。
cd "$(dirname "$0")" || exit 1
PY=/opt/anaconda3/envs/wrf-webvis/bin/python
LOG=.runtime/账号与子看板_测试日志.txt
mkdir -p .runtime
export PYTHONPATH="$PWD:$PWD/wrf_webvis:$PWD/处理数据:$PWD/处理数据_v3"
export MPLBACKEND=Agg
{
  echo "start $(date)"; "$PY" --version
  echo "===== 1. 本次相关测试"
  "$PY" -m pytest -q -p no:cacheprovider -rf \
    wrf_webvis/tests/test_auth.py \
    wrf_webvis/tests/test_dashboard_tree.py \
    wrf_webvis/tests/test_access_control.py \
    wrf_webvis/tests/test_image_annotations.py \
    wrf_webvis/tests/test_response_compression.py \
    wrf_webvis/tests/test_root_route.py \
    wrf_webvis/tests/test_recent_logs.py \
    wrf_webvis/tests/test_dashboard_deletion_ui.py \
    wrf_webvis/tests/test_web_parameter_controls.py \
    tests/test_dashboard_store_migration.py \
    tests/test_dashboard_image_order.py
  echo "EXIT_RELEVANT $?"
  echo "===== 2. 前端测试"
  fail=0
  for f in wrf_webvis/tests/*.mjs; do node --test "$f" > /tmp/wrfwebvis_node_test.txt 2>&1 || { fail=1; echo "FAILED $f"; tail -40 /tmp/wrfwebvis_node_test.txt; }; done
  echo "EXIT_NODE $fail"
  echo "===== 3. 全部测试"
  "$PY" -m pytest -q -p no:cacheprovider -rf tests wrf_webvis/tests 处理数据_v3/tests
  echo "EXIT_FULL $?"
  echo "end $(date)"
} > "$LOG" 2>&1
grep -E "^EXIT_|passed|failed" "$LOG" | tail -8
echo "测试完成，完整结果在 $PWD/$LOG ，可以关闭此窗口。"
