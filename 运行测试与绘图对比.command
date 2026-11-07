#!/bin/bash
# Claude 2026-11-01：绘图重构（拆掉“旧菜单”翻译层）用。只跑测试和回归对比，不重启网页、不写看板、不处理 wrfout。
# 1) 全部测试；2) 用看板里 20 张代表性请求重画：第一次运行存为改动前基线 before，以后每次与 before 逐像素、逐数值比对。
# 结果写入 .runtime/绘图回归/latest.log（Claude 直接读）。
cd "$(dirname "$0")" || exit 1
PY=/opt/anaconda3/envs/wrf-webvis/bin/python
DIR=.runtime/绘图回归
mkdir -p "$DIR"
STAMP=$(date +%Y%m%d_%H%M%S)
LOG="$DIR/$STAMP.log"
export PYTHONPATH="$PWD:$PWD/wrf_webvis:$PWD/处理数据:$PWD/处理数据_v3"
export MPLBACKEND=Agg
{
  echo "start $(date)"; "$PY" --version
  echo "git $(git rev-parse --short HEAD) 改动文件数 $(git status --short | wc -l)"
  echo "===== 1. Python 全部测试"
  "$PY" -m pytest -q -p no:cacheprovider --continue-on-collection-errors -rfE tests wrf_webvis/tests 处理数据_v3/tests
  echo "EXIT_PYTEST $?"
  echo "===== 2. 前端测试"
  node --test wrf_webvis/tests/*.mjs 2>&1 | tail -12
  echo "EXIT_NODE ${PIPESTATUS[0]}"
  echo "===== 3. wrf-lab 测试"
  echo "===== 4. 绘图回归对比"
  if [ ! -f "$DIR/before/summary.json" ]; then
    echo "没有基线：先从看板挑数据齐全的请求，再重画存为 before"
    [ -f "$DIR/requests.json" ] || "$PY" server_tools/plot_regression.py select
    "$PY" server_tools/plot_regression.py capture --label before
  else
    "$PY" server_tools/plot_regression.py capture --label "after_$STAMP" && \
    "$PY" server_tools/plot_regression.py compare --base before --label "after_$STAMP"
    echo "EXIT_COMPARE $?"
  fi
  echo "end $(date)"
} > "$LOG" 2>&1
cp "$LOG" "$DIR/latest.log"
echo "完成，结果在 $PWD/$LOG ，可以关闭此窗口。"
