#!/bin/bash
# Claude 2026-11-02：处理套餐、数据集 post、内核区风切变 shear_core_200_850_*；下午加：页面重排、并行处理、批量提交、读取加速；11-03 加：完成情况索引、提交复用预览计划。
# 用 conda wrf-webvis 环境跑测试；Python 测试、wrf-lab 测试和前端测试都通过才重启网页。结果写到 .runtime/claude_test_log_presets.txt
cd "$(dirname "$0")" || exit 1
PY=/opt/anaconda3/envs/wrf-webvis/bin/python
LOG=.runtime/claude_test_log_presets.txt
export PYTHONPATH="$PWD:$PWD/wrf_webvis:$PWD/处理数据:$PWD/处理数据_v3"
export MPLBACKEND=Agg
mkdir -p .runtime
{
  echo "start $(date)"; "$PY" --version
  echo "===== 1. 本次相关测试"
  "$PY" -m pytest -q -p no:cacheprovider \
    处理数据_v3/tests/test_shear_core.py \
    处理数据_v3/tests/test_processing_presets.py \
    处理数据_v3/tests/test_fast_source_reads.py \
    处理数据_v3/tests/test_completion_index.py \
    处理数据_v3/tests/test_storm_relative_wind.py \
    处理数据_v3/tests/test_project_config.py \
    处理数据_v3/tests/test_pilot_entrypoint.py \
    wrf_webvis/tests/test_v3_management_api.py \
    wrf_webvis/tests/test_v3_management.py
  echo "EXIT_RELEVANT $?"
  echo "===== 1b. 快速读取与原 getvar 对照（只读一个真实 wrfout 时次，不写结果；外接盘没接上时跳过）"
  "$PY" server_tools/check_fast_reads.py WRF_315_land 3
  echo "EXIT_FAST_READS $?"
  echo "===== 2. 全部 Python 测试"
  "$PY" -m pytest -q -p no:cacheprovider -rf tests wrf_webvis/tests 处理数据_v3/tests
  FULL=$?
  echo "EXIT_FULL $FULL"
  echo "===== 3. wrf-lab 测试"
  LAB=$?
  echo "EXIT_LAB $LAB"
  echo "===== 4. 前端测试"
  NODE="$(command -v node || ls /opt/homebrew/bin/node /usr/local/bin/node 2>/dev/null | head -n 1)"
  NODE_OK=0
  if [ -n "$NODE" ]; then "$NODE" --test wrf_webvis/tests/*.mjs; NODE_OK=$?; echo "EXIT_NODE_ALL $NODE_OK"; else echo "没有找到 node，跳过前端测试"; fi
  echo "===== 5. 重启网页"
  if [ "$FULL" -eq 0 ] && [ "$LAB" -eq 0 ] && [ "$NODE_OK" -eq 0 ]; then ./restart_web.sh; echo "EXIT_RESTART $?"; else echo "测试没有全部通过，没有重启网页"; fi
  echo "end $(date)"
} > "$LOG" 2>&1
echo "测试完成，结果在 $PWD/$LOG ，可以关闭此窗口。"
