#!/bin/bash

set -euo pipefail

PROJECT_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
LABEL="${WRF_WEBVIS_LAUNCH_LABEL:-com.zhuangyifan.wrfwebvis.uvicorn}"
UID_VALUE="${WRF_WEBVIS_TEST_UID:-$(id -u)}"
DOMAIN="gui/${UID_VALUE}"
SERVICE="${DOMAIN}/${LABEL}"
PLIST="${WRF_WEBVIS_LAUNCH_AGENT_PLIST:-${HOME}/Library/LaunchAgents/${LABEL}.plist}"
PORT="${WRF_WEBVIS_PORT:-8000}"
HEALTH_URL="${WRF_WEBVIS_HEALTH_URL:-http://127.0.0.1:${PORT}/}"
MAX_ATTEMPTS="${WRF_WEBVIS_MAX_ATTEMPTS:-30}"
WAIT_SECONDS="${WRF_WEBVIS_WAIT_SECONDS:-1}"
EXPECTED_COMMAND="wrf_webvis.app.main:app"

die() {
    printf '错误：%s\n' "$*" >&2
    exit 1
}

[[ -f "$PLIST" ]] || die "LaunchAgent 配置不存在：${PLIST}"
[[ "$PORT" =~ ^[0-9]+$ ]] || die "端口必须是整数：${PORT}"
[[ "$MAX_ATTEMPTS" =~ ^[1-9][0-9]*$ ]] || die "健康检查次数必须是正整数"

# Validate listeners before changing service state. This prevents the script from
# stopping an unrelated application that happens to use the configured port.
listener_pids="$(lsof -tiTCP:"$PORT" -sTCP:LISTEN 2>/dev/null || true)"
for pid in $listener_pids; do
    [[ "$pid" =~ ^[0-9]+$ ]] || die "无法识别端口 ${PORT} 的监听进程：${pid}"
    command_line="$(ps -p "$pid" -o command= 2>/dev/null || true)"
    process_cwd="$(lsof -a -p "$pid" -d cwd -Fn 2>/dev/null | sed -n 's/^n//p' | head -n 1)"
    if [[ "$command_line" != *"$EXPECTED_COMMAND"* || "$process_cwd" != "$PROJECT_ROOT" ]]; then
        die "端口 ${PORT} 被不属于本项目的进程占用（PID ${pid}），已取消重启"
    fi
done

printf '正在停止 WRF WebVis...\n'
launchctl bootout "$SERVICE" 2>/dev/null || true

# A previously detached process may no longer be tracked by launchd. Only stop
# PIDs that were verified above as this project's server and still own the port.
for pid in $listener_pids; do
    if lsof -a -p "$pid" -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
        kill "$pid"
        for _ in 1 2 3 4 5; do
            kill -0 "$pid" 2>/dev/null || break
            sleep "$WAIT_SECONDS"
        done
        if kill -0 "$pid" 2>/dev/null; then
            kill -KILL "$pid"
        fi
    fi
done

remaining_pid="$(lsof -tiTCP:"$PORT" -sTCP:LISTEN 2>/dev/null | head -n 1 || true)"
[[ -z "$remaining_pid" ]] || die "端口 ${PORT} 仍被 PID ${remaining_pid} 占用"

printf '正在启动 WRF WebVis...\n'
launchctl bootstrap "$DOMAIN" "$PLIST"
launchctl kickstart "$SERVICE"

for ((attempt = 1; attempt <= MAX_ATTEMPTS; attempt++)); do
    if curl -fsS --max-time 2 "$HEALTH_URL" >/dev/null; then
        printf '网页已重启：%s\n' "$HEALTH_URL"
        exit 0
    fi
    sleep "$WAIT_SECONDS"
done

printf '最近的错误日志：\n' >&2
tail -n 20 "${HOME}/Library/Logs/WRFWebVis/uvicorn-error.log" 2>/dev/null >&2 || true
die "服务未在预期时间内通过健康检查：${HEALTH_URL}"
