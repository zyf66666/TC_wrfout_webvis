#!/bin/bash
# 功能说明：给服务器上 wrfwebvis.zhuangyifan.com 的 nginx 配置加上 client_max_body_size 200m
# （网页上传大于 1 MB 的 PDF 需要它）。只加这一行，不动其他设置；改前在服务器上备份，
# nginx -t 检查不通过就自动还原。双击运行，过程中会要求输入服务器的 sudo 密码。
# 2026-10-22 Claude 编写。兼容 macOS 自带的 bash 3.2。

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
# 在服务器上运行：找到 wrfwebvis 的 nginx 配置，在 443 server 块的 server_name 之后加一行。
set -u
SUDO=${SUDO-sudo}
GLOBS=${NGINX_GLOBS:-"/etc/nginx/sites-enabled/* /etc/nginx/conf.d/*.conf"}
BACKUP_DIR=${BACKUP_DIR:-/var/backups/nginx-wrfwebvis}
NGINX_TEST=${NGINX_TEST:-"nginx -t"}
NGINX_RELOAD=${NGINX_RELOAD:-"systemctl reload nginx"}
LINE="    client_max_body_size 200m;"

F=""
for f in $GLOBS; do
  [ -f "$f" ] || continue
  if $SUDO grep -q "server_name[[:space:]]*wrfwebvis.zhuangyifan.com" "$f"; then F=$(readlink -f "$f"); break; fi
done
[ -n "$F" ] || { echo "没找到含 server_name wrfwebvis.zhuangyifan.com 的 nginx 配置（找过：$GLOBS）"; exit 1; }
echo "服务器上的配置文件：$F"

if $SUDO grep -q "client_max_body_size" "$F"; then
  echo "已经有 client_max_body_size，不用改："
  $SUDO grep -n "client_max_body_size" "$F"
  exit 0
fi

TMP=$(mktemp)
$SUDO awk -v line="$LINE" '
  /^[[:space:]]*server[[:space:]]*\{/ { in443 = 0 }
  /listen[[:space:]]+(\[::\]:)?443/ { in443 = 1 }
  { print }
  in443 && !done && /server_name[[:space:]]+wrfwebvis\.zhuangyifan\.com/ { print line; done = 1 }
  END { if (!done) exit 3 }
' "$F" > "$TMP"
status=$?
if [ $status -ne 0 ]; then
  echo "没找到 listen 443 的 server 块，不改（awk 退出码 $status）。"; rm -f "$TMP"; exit 1
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
if ! scp "${SCP_ARGS[@]}" -q "$REMOTE_SCRIPT" "$HOST:/tmp/wrfwebvis_nginx_body_size.sh"; then
  echo "上传失败：检查网络和 SSH 登录。"; rm -f "$REMOTE_SCRIPT"; pause_and_exit 1
fi
rm -f "$REMOTE_SCRIPT"
echo "在服务器上执行（可能会要求输入 sudo 密码）……"
ssh -t "${SSH_ARGS[@]}" "$HOST" "bash /tmp/wrfwebvis_nginx_body_size.sh; s=\$?; rm -f /tmp/wrfwebvis_nginx_body_size.sh; exit \$s"
result=$?
echo
if [ $result -eq 0 ]; then
  echo "好了。可以在公网入口上传一个大于 1 MB 的 PDF 试一下。"
else
  echo "没有改成（退出码 $result），把这个窗口的内容发给 Claude。"
fi
pause_and_exit $result
