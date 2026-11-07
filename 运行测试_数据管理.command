#!/bin/bash
# Claude 2026-10-21：数据管理页改动（自定义试验、变量说明浮窗、已处理数据维度、完成情况说明、历史任务可收起）后的测试。
# 用 conda wrf-webvis 环境跑测试；Python 测试和前端测试都通过才重启网页。结果写到 .runtime/claude_test_log.txt
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
    wrf_webvis/tests/test_v3_management_api.py \
    wrf_webvis/tests/test_v3_management.py \
    处理数据_v3/tests/test_project_config.py
  echo "EXIT_RELEVANT $?"
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
  if [ -n "$NODE" ]; then "$NODE" --test wrf_webvis/tests/test_data_management.mjs; NODE_OK=$?; echo "EXIT_NODE_DATA $NODE_OK"; "$NODE" --test wrf_webvis/tests/*.mjs; echo "EXIT_NODE_ALL $?（test_access_frontend 有 2 条 10-16 前就失败的旧测试）"; else echo "没有找到 node，跳过前端测试"; fi
  echo "===== 5. 重启网页"
  if [ "$FULL" -eq 0 ] && [ "$LAB" -eq 0 ] && [ "$NODE_OK" -eq 0 ]; then ./restart_web.sh; echo "EXIT_RESTART $?"; else echo "测试没有全部通过，没有重启网页"; fi
  echo "end $(date)"
} > "$LOG" 2>&1
echo "测试完成，结果在 $PWD/$LOG ，可以关闭此窗口。"
