#!/bin/bash
# Claude 2026-11-06：图片拖到别的看板（store.transfer_image、POST /images/{id}/transfer、app.js 拖放）改动的测试。
# 相关测试和前端测试都通过才重启网页；全部测试只作参考（11-06 已有 2 条与本次无关的旧失败：缩略图、v3 读取）。
# 结果写入 .runtime/图片跨看板_测试日志.txt
cd "$(dirname "$0")" || exit 1
PY=/opt/anaconda3/envs/wrf-webvis/bin/python
LOG=.runtime/图片跨看板_测试日志.txt
mkdir -p .runtime
export PYTHONPATH="$PWD:$PWD/wrf_webvis:$PWD/处理数据:$PWD/处理数据_v3"
export MPLBACKEND=Agg
{
  echo "start $(date)"; "$PY" --version
  echo "===== 1. 本次相关测试"
  "$PY" -m pytest -q -p no:cacheprovider -rf \
    tests/test_dashboard_image_order.py \
    tests/test_dashboard_store_migration.py \
    tests/test_dashboard_store_concurrency.py \
    wrf_webvis/tests/test_dashboard_deletion_ui.py \
    wrf_webvis/tests/test_web_parameter_controls.py \
    wrf_webvis/tests/test_access_control.py \
    wrf_webvis/tests/test_image_annotations.py
  REL=$?
  echo "EXIT_RELEVANT $REL"
  echo "===== 2. 前端测试"
  NODE="$(command -v node || ls /opt/homebrew/bin/node /usr/local/bin/node 2>/dev/null | head -n 1)"
  fail=0
  for f in wrf_webvis/tests/*.mjs; do "$NODE" --test "$f" > /tmp/wrfwebvis_node_test.txt 2>&1 || { fail=1; echo "FAILED $f"; tail -40 /tmp/wrfwebvis_node_test.txt; }; done
  echo "EXIT_NODE $fail"
  echo "===== 3. 全部测试（参考）"
  "$PY" -m pytest -q -p no:cacheprovider -rf tests wrf_webvis/tests 处理数据_v3/tests
  echo "EXIT_FULL $?"
  echo "===== 4. 重启网页"
  if [ "$REL" -eq 0 ] && [ "$fail" -eq 0 ]; then ./restart_web.sh; echo "EXIT_RESTART $?"; else echo "相关测试没有全部通过，没有重启网页"; fi
  echo "end $(date)"
} > "$LOG" 2>&1
grep -E "^EXIT_|passed|failed|没有重启" "$LOG" | tail -10
echo "测试完成，完整结果在 $PWD/$LOG ，可以关闭此窗口。"
