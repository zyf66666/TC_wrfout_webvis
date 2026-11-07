#!/bin/bash
# 功能说明：网页改为账号登录后（2026-11-06），去掉服务器 nginx 配置里按 IP 拦截写请求的
# `if ($wrfwebvis_block_write) { return 403 ...; }`——不去掉的话公网上连“登录”请求都会被挡。
# 只删这一段，不动其他设置（geo/map 定义留着不用也没关系）；改前在服务器上备份，
# nginx -t 检查不通过就自动还原。双击运行，过程中会要求输入服务器的 sudo 密码。
# 先确认本机网页已经是新版（公网打开会跳到登录页），再运行本脚本。
# 2026-11-06 Claude 编写（服务器查找部分沿用 更新服务器nginx配置.command）。兼容 macOS 自带的 bash 3.2。

cd "$(dirname "$0")" || exit 1

pause_and_exit() {
  echo
  read -r -p "按回车关闭窗口……" _
  exit "${1:-0}"
}

# ---------- 1. 找服务器地址：从 SSH 隧道的 LaunchAgent 里找含 18000 端口的 ssh 命令 ----------
HOST=""
SSH_PORT=""
SSH_ID=""

# 功能说明：从 ssh/autossh 的参数里取出目标主机、端口和私钥。
parse_ssh_words() {
  local seen=0 skip=0 prev="" w
  for w in "$@"; do
    w=${w//\'/}
    w=${w//\"/}
    if [ $seen -eq 0 ]; then
      case "${w##*/}" in ssh|autossh) seen=1 ;; esac
      continue
    fi
    if [ $skip -eq 1 ]; then
      case "$prev" in
        -p) SSH_PORT=$w ;;
        -i) SSH_ID=$w ;;
      esac
      skip=0
      continue
    fi
    case "$w" in
      -[RLDipoFlJWbcEemOQSwBIM]) prev=$w; skip=1 ;;
      -*) ;;
      *) HOST=$w; return 0 ;;
    esac
  done
  return 1
}

find_host() {
  local p words
  for p in "$HOME"/Library/LaunchAgents/*.plist; do
    [ -f "$p" ] || continue
    grep -q "18000" "$p" 2>/dev/null || continue
    words=$(/usr/libexec/PlistBuddy -c 'Print :ProgramArguments' "$p" 2>/dev/null | sed -e '1d' -e '$d' -e 's/^[[:space:]]*//')
    [ -n "$words" ] || continue
    # shellcheck disable=SC2086
    if parse_ssh_words $words; then
      echo "从 $(basename "$p") 找到隧道服务器：$HOST${SSH_PORT:+（端口 $SSH_PORT）}"
      return 0
    fi
  done
  return 1
}

if [ -n "${WRF_WEBVIS_SERVER:-}" ]; then
  HOST=$WRF_WEBVIS_SERVER
elif find_host; then
  read -r -p "用这个服务器吗？[Y/n] " ans
  case "$ans" in n|N) HOST="" ;; esac
fi
if [ -z "$HOST" ]; then
  read -r -p "请输入服务器的 SSH 地址（如 user@1.2.3.4，或 ~/.ssh/config 里的别名）：" HOST
fi
[ -n "$HOST" ] || { echo "没有服务器地址，退出。"; pause_and_exit 1; }

SSH_ARGS=()
SCP_ARGS=()
[ -n "$SSH_PORT" ] && { SSH_ARGS+=(-p "$SSH_PORT"); SCP_ARGS+=(-P "$SSH_PORT"); }
[ -n "$SSH_ID" ] && { SSH_ARGS+=(-i "$SSH_ID"); SCP_ARGS+=(-i "$SSH_ID"); }

# ---------- 2. 服务器上执行的脚本 ----------
REMOTE_SCRIPT=$(mktemp -t wrfwebvis_nginx)
cat > "$REMOTE_SCRIPT" <<'REMOTE'
#!/bin/bash
# 在服务器上运行：找到 wrfwebvis 的 nginx 配置，删掉 if ($wrfwebvis_block_write) { ... } 这一段。
set -u
# 以 root 登录时不需要（也可能没装）sudo
if [ -z "${SUDO+x}" ]; then
  if [ "$(id -u)" = "0" ]; then SUDO=""; else SUDO=sudo; fi
fi
GLOBS=${NGINX_GLOBS:-"/etc/nginx/sites-enabled/* /etc/nginx/conf.d/*.conf"}
BACKUP_DIR=${BACKUP_DIR:-/var/backups/nginx-wrfwebvis}
NGINX_TEST=${NGINX_TEST:-"nginx -t"}
NGINX_RELOAD=${NGINX_RELOAD:-"systemctl reload nginx"}

F=""
for f in $GLOBS; do
  [ -f "$f" ] || continue
  if $SUDO grep -q "server_name[[:space:]]*wrfwebvis.zhuangyifan.com" "$f"; then F=$(readlink -f "$f"); break; fi
done
[ -n "$F" ] || { echo "没找到含 server_name wrfwebvis.zhuangyifan.com 的 nginx 配置（找过：$GLOBS）"; exit 1; }
echo "服务器上的配置文件：$F"

if ! $SUDO grep -q 'if[[:space:]]*(\$wrfwebvis_block_write)' "$F"; then
  echo "配置里已经没有按 IP 拦截写请求的那一段，不用改。"
  exit 0
fi

TMP=$(mktemp)
$SUDO awk '
  skip == 0 && /if[[:space:]]*\(\$wrfwebvis_block_write\)/ { skip = 1; removed++; next }
  skip == 1 { if ($0 ~ /^[[:space:]]*}[[:space:]]*$/) { skip = 0 } ; next }
  { print }
  END { if (!removed || skip) exit 3 }
' "$F" > "$TMP"
status=$?
if [ $status -ne 0 ]; then
  echo "没能安全识别要删的那一段，不改（awk 退出码 $status）。"; rm -f "$TMP"; exit 1
fi

$SUDO mkdir -p "$BACKUP_DIR"
B="$BACKUP_DIR/$(basename "$F").$(date +%Y%m%d-%H%M%S)"
$SUDO cp -p "$F" "$B" || { echo "备份失败，不改。"; rm -f "$TMP"; exit 1; }
echo "已备份到：$B"
$SUDO cp "$TMP" "$F"
rm -f "$TMP"
echo "改动："
diff "$B" "$F" 2>/dev/null || $SUDO diff "$B" "$F"

if $SUDO $NGINX_TEST; then
  $SUDO $NGINX_RELOAD || $SUDO nginx -s reload
  echo "完成：nginx 已重新加载。"
else
  echo "nginx -t 没通过，已还原原配置。"
  $SUDO cp -p "$B" "$F"
  exit 1
fi
REMOTE

echo
echo "上传检查脚本到 $HOST ……"
# 不用 scp（有的服务器关了 SFTP，scp 会报 Connection closed），直接经 ssh 管道写过去。
if ! ssh "${SSH_ARGS[@]}" "$HOST" "cat > /tmp/wrfwebvis_nginx_login.sh" < "$REMOTE_SCRIPT"; then
  echo "上传失败：检查网络和 SSH 登录（可以先在终端试 ssh ${SSH_ARGS[*]} $HOST）。"; rm -f "$REMOTE_SCRIPT"; pause_and_exit 1
fi
rm -f "$REMOTE_SCRIPT"
echo "在服务器上执行（可能会要求输入 sudo 密码）……"
ssh -t "${SSH_ARGS[@]}" "$HOST" "bash /tmp/wrfwebvis_nginx_login.sh; s=\$?; rm -f /tmp/wrfwebvis_nginx_login.sh; exit \$s"
result=$?
echo
if [ $result -eq 0 ]; then
  echo "好了。用手机流量或别的网络打开 https://wrfwebvis.zhuangyifan.com ，应先看到登录页，登录后能正常使用。"
else
  echo "没有改成（退出码 $result），把这个窗口的内容发给 Claude。"
fi
pause_and_exit $result
