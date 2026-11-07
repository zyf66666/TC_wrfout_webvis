#!/bin/bash
# 重新生成上传超算用的 tc_hourly_sc.tar.gz：超算端/ 的脚本 + 当前 ../处理数据_v3 管线的原样拷贝。
# 本地管线（中心算法、变量算法）改过之后重新打包一次，保证超算和本地算法一致。
#   bash 打包.sh                       # 用同级的 ../处理数据_v3
#   PIPELINE_SRC=/路径/处理数据_v3 bash 打包.sh
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC="${PIPELINE_SRC:-$HERE/../处理数据_v3}"
OUT="${OUT_TAR:-$HERE/tc_hourly_sc.tar.gz}"
[ -d "$SRC/wrf_processing_v3" ] || { echo "找不到管线 $SRC/wrf_processing_v3"; exit 1; }

sha256() { if command -v shasum >/dev/null 2>&1; then shasum -a 256 "$@"; else sha256sum "$@"; fi; }
STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT
PKG="$STAGE/tc_hourly_sc"
mkdir -p "$PKG/pipeline"
cp "$HERE"/超算端/*.py "$HERE"/超算端/*.sh "$HERE"/超算端/*.sbatch "$HERE"/超算端/README.md "$PKG/"
for item in wrf_processing_v3 center_schemes.json project_config.py process_data.py processing_presets.json VARIABLES_REFERENCE.md; do
    cp -R "$SRC/$item" "$PKG/pipeline/"
done
find "$PKG" -name '__pycache__' -type d -prune -exec rm -rf {} +
find "$PKG" -name '.DS_Store' -delete

# 版本信息：打包时间 + 管线文件的指纹（与本地哪一版一致）
{
    echo "tc_hourly_sc 打包于 $(date '+%Y-%m-%d %H:%M:%S')"
    if git -C "$SRC" rev-parse --short HEAD >/dev/null 2>&1; then
        echo "wrf-webvis git $(git -C "$SRC" rev-parse --short HEAD)$( [ -n "$(git -C "$SRC" status --porcelain -- . 2>/dev/null)" ] && echo '（含未提交改动）')"
    fi
    echo "pipeline sha256 $( (cd "$PKG/pipeline" && find . -type f -name '*.py' -o -type f -name '*.json' | LC_ALL=C sort | xargs -I{} sh -c 'echo {}; cat "{}"' ) | sha256 | cut -c1-16)"
} > "$PKG/VERSION.txt"

chmod +x "$PKG"/*.sh "$PKG"/*.py
# COPYFILE_DISABLE：macOS 打包时不带 ._ 隐藏文件
COPYFILE_DISABLE=1 tar -czf "$OUT" -C "$STAGE" tc_hourly_sc
echo "已生成 $OUT（$(du -h "$OUT" | cut -f1)）"
cat "$PKG/VERSION.txt"
