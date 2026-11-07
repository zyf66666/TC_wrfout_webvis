#!/bin/bash
# Claude 2026-10-26：水平图 RMW 改读 processed_v3（rmw_source / rmw_height_m / rmw_annotate）、
# 新增超梯度诊断变量 pgf_r、cen_r、cor_r、motf_r、agf_r 后的测试。
# 用 conda wrf-webvis 环境跑；全部 Python 测试与 wrf-lab 测试都通过才重启网页。结果写到 .runtime/claude_test_log.txt
cd "$(dirname "$0")" || exit 1
PY=/opt/anaconda3/envs/wrf-webvis/bin/python
LOG=.runtime/claude_test_log.txt
export PYTHONPATH="$PWD:$PWD/wrf_webvis:$PWD/处理数据:$PWD/处理数据_v3"
export MPLBACKEND=Agg
mkdir -p .runtime
{
  echo "start $(date)"; "$PY" --version
  echo "===== 1. 本次相关测试"
  "$PY" -m pytest -q -p no:cacheprovider \
    wrf_webvis/tests/test_horizontal_rmw_v3.py \
    处理数据_v3/tests/test_agradient.py \
    wrf_webvis/tests/test_time_radius_rmw_source.py \
    wrf_webvis/tests/test_horizontal_center_v3.py \
    wrf_webvis/tests/test_plot_inputs.py \
    wrf_webvis/tests/test_web_parameter_controls.py \
    wrf_webvis/tests/test_dashboard_deletion_ui.py \
    处理数据_v3/tests/test_registry_planner.py \
    处理数据_v3/tests/test_project_config.py
  echo "EXIT_RELEVANT $?"
  echo "===== 2. 全部 Python 测试"
  "$PY" -m pytest -q -p no:cacheprovider -rf tests wrf_webvis/tests 处理数据_v3/tests
  FULL=$?
  echo "EXIT_FULL $FULL"
  echo "===== 3. wrf-lab 测试"
  LAB=$?
  echo "EXIT_LAB $LAB"
  echo "===== 4. 前端测试（只记录，不影响重启；test_access_frontend 有 2 条 10-16 前就失败的旧测试）"
  NODE="$(command -v node || ls /opt/homebrew/bin/node /usr/local/bin/node 2>/dev/null | head -n 1)"
  if [ -n "$NODE" ]; then "$NODE" --test wrf_webvis/tests/*.mjs; echo "EXIT_NODE_ALL $?"; else echo "没有找到 node，跳过前端测试"; fi
  echo "===== 5. 重启网页"
  if [ "$FULL" -eq 0 ] && [ "$LAB" -eq 0 ]; then ./restart_web.sh; echo "EXIT_RESTART $?"; else echo "测试没有全部通过，没有重启网页"; fi
  echo "end $(date)"
} > "$LOG" 2>&1
echo "测试完成，结果在 $PWD/$LOG ，可以关闭此窗口。"
