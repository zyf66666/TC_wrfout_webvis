#!/bin/bash
# 2026-11-04 Claude：绘图端自动识别网格间距（services/grid_detection.py）的测试。
# 先跑新测试和绘图相关测试，再跑全部 Python 与 wrf-lab 测试；全部通过才重启网页。结果写到 .runtime/claude_test_log_grid.txt。
cd "$(dirname "$0")" || exit 1
PY=/opt/anaconda3/envs/wrf-webvis/bin/python
LOG=.runtime/claude_test_log_grid.txt
export PYTHONPATH="$PWD:$PWD/wrf_webvis:$PWD/处理数据:$PWD/处理数据_v3"
export MPLBACKEND=Agg
mkdir -p .runtime
{
  echo "start $(date)"; "$PY" --version
  echo "===== 1. 网格识别与绘图相关测试"
  "$PY" -m pytest -q -p no:cacheprovider -rfs \
    wrf_webvis/tests/test_grid_detection.py \
    wrf_webvis/tests/test_processed_v3_reader.py \
    wrf_webvis/tests/test_paths.py \
    wrf_webvis/tests/test_draw_variables_v3.py \
    wrf_webvis/tests/test_radius_height_loading.py \
    wrf_webvis/tests/test_time_height_plot.py \
    wrf_webvis/tests/test_spatial_time_series.py \
    wrf_webvis/tests/test_one_dimensional_diagnostics.py \
    wrf_webvis/tests/test_center_scheme_plotting.py \
    wrf_webvis/tests/test_layer_center_variable.py \
    tests/test_plot_only_policy.py
  echo "EXIT_RELEVANT $?"
  echo "===== 2. 全部 Python 测试"
  "$PY" -m pytest -q -p no:cacheprovider -rf tests wrf_webvis/tests 处理数据_v3/tests
  FULL=$?
  echo "EXIT_FULL $FULL"
  echo "===== 3. wrf-lab 测试"
  LAB=$?
  echo "EXIT_LAB $LAB"
  echo "===== 4. 重启网页"
  if [ "$FULL" -eq 0 ] && [ "$LAB" -eq 0 ]; then ./restart_web.sh; echo "EXIT_RESTART $?"; else echo "测试没有全部通过，没有重启网页"; fi
  echo "end $(date)"
} > "$LOG" 2>&1
echo "测试完成，结果在 $PWD/$LOG ，可以关闭此窗口。"
