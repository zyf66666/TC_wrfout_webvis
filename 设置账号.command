#!/bin/bash
# 功能说明：管理网页登录账号（2026-11-06 Claude 编写）。双击运行：
#   1) 设置 root 账号密码（第一次用：公网上要用它登录才有全部权限）
#   2) 设置任意账号的密码（账号不存在时新建为普通账号，看板权限之后在网页“账号管理”里勾）
#   3) 列出全部账号
# 账号存在 wrf_webvis/storage_data/users.sqlite（只存密码哈希）。本机直接打开 127.0.0.1:8000 不需要登录。
cd "$(dirname "$0")" || exit 1
PY=/opt/anaconda3/envs/wrf-webvis/bin/python
[ -x "$PY" ] || PY=$(command -v python3)
export PYTHONPATH="$PWD"

echo "网页登录账号管理"
echo "  1) 设置 root 账号的密码"
echo "  2) 设置某个账号的密码（不存在就新建普通账号）"
echo "  3) 列出全部账号"
read -r -p "选 1/2/3： " choice
case "$choice" in
  1) "$PY" -m wrf_webvis.app.cli users set-password root --role root ;;
  2) read -r -p "用户名： " name
     [ -n "$name" ] && "$PY" -m wrf_webvis.app.cli users set-password "$name" ;;
  3) "$PY" -m wrf_webvis.app.cli users list ;;
  *) echo "没有选择，退出。" ;;
esac
echo
read -r -p "按回车关闭窗口……" _
