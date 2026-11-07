#!/bin/bash
# Claude 2026-10-09（10-13 加入水平图中心测试；10-26 加入水平图 RMW 读 v3、超梯度诊断变量测试）：用 conda wrf-webvis 环境跑 webvis 测试，结果写入 .runtime/claude_test_log.txt
cd "$(dirname "$0")" || exit 1
PY=/opt/anaconda3/envs/wrf-webvis/bin/python
LOG=.runtime/claude_test_log.txt
export PYTHONPATH="$PWD:$PWD/wrf_webvis:$PWD/处理数据:$PWD/处理数据_v3"
export MPLBACKEND=Agg
{
  echo "start $(date)"; "$PY" --version
  echo "===== 1. 本次相关测试"
  "$PY" -m pytest -q -p no:cacheprovider \
    wrf_webvis/tests/test_horizontal_center_v3.py \
    wrf_webvis/tests/test_horizontal_rmw_v3.py \
    处理数据_v3/tests/test_agradient.py \
    wrf_webvis/tests/test_track_plot.py \
    wrf_webvis/tests/test_draw_variables_v3.py \
    wrf_webvis/tests/test_time_radius_rmw_source.py \
    wrf_webvis/tests/test_plot_inputs.py \
    wrf_webvis/tests/test_radius_height_format.py \
    wrf_webvis/tests/test_processed_v3_reader.py \
    wrf_webvis/tests/test_web_parameter_controls.py
  echo "EXIT_RELEVANT $?"
  echo "===== 2. 全部测试"
  "$PY" -m pytest -q -p no:cacheprovider -rf tests wrf_webvis/tests 处理数据_v3/tests
  echo "EXIT_FULL $?"
  echo "end $(date)"
} > "$LOG" 2>&1
echo "测试完成，结果在 $PWD/$LOG ，可以关闭此窗口。"
