#!/usr/bin/env bash
# Windows 离线 release 打包脚本（macOS / Linux 版本）
#
# 与 scripts/build_offline_windows.ps1 等价：在非 Windows 机器上交叉打包出
# Windows 用的离线 zip。pip download 用 --platform win_amd64 / --python-version 3.12
# 拉 Windows wheel，本机环境（Python 3.9 / mac）不会污染产物。
#
# 用法：
#   bash scripts/build_offline_windows.sh                  # 默认版本 dev
#   bash scripts/build_offline_windows.sh -v 0.1.0
#   bash scripts/build_offline_windows.sh -v 0.1.0 --skip-frontend
#   bash scripts/build_offline_windows.sh -v 0.1.0 --skip-wheels
#
# 目标机器只需要：Python 3.12 + 解压 zip + 双击 install.bat / start.bat。

set -euo pipefail

VERSION="dev"
SKIP_FRONTEND=0
SKIP_WHEELS=0
OUTPUT_DIR="$(pwd)"

while [[ $# -gt 0 ]]; do
    case "$1" in
        -v|--version) VERSION="$2"; shift 2 ;;
        --skip-frontend) SKIP_FRONTEND=1; shift ;;
        --skip-wheels) SKIP_WHEELS=1; shift ;;
        -o|--output) OUTPUT_DIR="$2"; shift 2 ;;
        *) echo "未知参数：$1" >&2; exit 1 ;;
    esac
done

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

echo "=== DataOps Studio 离线 release 打包 ==="
echo "仓库根：$REPO_ROOT"
echo "版本号：$VERSION"
echo ""

STAGING_NAME="DataOpsStudio-win-offline-${VERSION}"
STAGING_DIR="${TMPDIR:-/tmp}/${STAGING_NAME}"
ZIP_PATH="${OUTPUT_DIR%/}/${STAGING_NAME}.zip"

if [[ -d "${STAGING_DIR}" ]]; then
    echo "清理旧 staging：${STAGING_DIR}"
    rm -rf "${STAGING_DIR}"
fi
mkdir -p "${STAGING_DIR}"

# ---- 1. 前端构建 ----
if [[ $SKIP_FRONTEND -eq 0 ]]; then
    echo "[1/5] 构建前端..."
    pushd frontend/frontend > /dev/null
    if [[ ! -d node_modules ]]; then
        npm ci --no-audit --no-fund
    fi
    npm run build
    popd > /dev/null
else
    echo "[1/5] 跳过前端构建（--skip-frontend）"
fi

if [[ ! -f static/spa/index.html ]]; then
    echo "[ERROR] static/spa/index.html 不存在，前端 build 失败或被跳过；离线包不可用" >&2
    exit 1
fi

# ---- 2. 下载 Python wheels（target = win_amd64 / Python 3.12）----
WHEELS_DIR="${STAGING_DIR}/wheels"
mkdir -p "${WHEELS_DIR}"

# 跨平台下 wheel 必须用比较新的 pip（>=22 才稳定支持 --platform 配合 --only-binary=:all:）。
# macOS 系统默认 pip 21.2.4 会忽略 --platform 直接拉本机 wheel —— 所以这里无脑
# bootstrap 一个隔离的 venv 用最新 pip，避免污染用户环境。
PIP_BOOTSTRAP_VENV="${STAGING_DIR}/_pip_bootstrap_venv"
PIP_CMD=""
if [[ $SKIP_WHEELS -eq 0 ]]; then
    if ! command -v python3 > /dev/null 2>&1; then
        echo "  [ERROR] 找不到 python3，无法 bootstrap pip" >&2
        exit 1
    fi
    echo "  bootstrap 新版 pip 到隔离 venv：${PIP_BOOTSTRAP_VENV}"
    python3 -m venv "${PIP_BOOTSTRAP_VENV}" > /dev/null
    "${PIP_BOOTSTRAP_VENV}/bin/pip" install --upgrade pip --quiet
    PIP_CMD="${PIP_BOOTSTRAP_VENV}/bin/pip"
    echo "  使用：$($PIP_CMD --version)"
fi

if [[ $SKIP_WHEELS -eq 0 ]]; then
    echo "[2/5] 下载 Python wheels（target: win_amd64 / Python 3.12）..."
    # dmPython 没 PyPI wheel —— 拆出来 best-effort 不当致命错误
    # uvicorn[standard] 的 uvloop extra 在 pip 新 resolver 里跟 --platform 不兼容
    # （marker `sys_platform != "win32"` 不被尊重），改成显式列 Windows 用的 extras
    REQ_CORE="${STAGING_DIR}/_requirements_core.txt"
    grep -v -E '^\s*(dmPython|uvicorn\[standard\]|#|$)' requirements.txt | \
        grep -v '^\s*$' > "$REQ_CORE" || true
    cat >> "$REQ_CORE" <<'EOF'
uvicorn>=0.27
httptools>=0.5.0
python-dotenv>=0.13
watchfiles>=0.13
websockets>=10.4
colorama>=0.4
EOF
    if ! $PIP_CMD download -r "$REQ_CORE" -d "${WHEELS_DIR}" \
            --platform win_amd64 --platform any \
            --python-version 3.12 \
            --implementation cp --only-binary=:all: 2>&1 | tail -20; then
        echo "  [ERROR] win_amd64 wheel 下载失败 —— 检查网络 / 代理" >&2
        exit 1
    fi
    rm -f "$REQ_CORE"
    # 清理 bootstrap venv（zip 里不要带它）
    rm -rf "${PIP_BOOTSTRAP_VENV}"
else
    echo "[2/5] 跳过 wheels 下载（--skip-wheels）"
fi

# ---- 3. 拷源码到 staging ----
echo "[3/5] 拷贝源码到 staging..."

# 目录类
for d in app static/spa init_db; do
    if [[ ! -e "$d" ]]; then echo "缺少：$d" >&2; exit 1; fi
    mkdir -p "${STAGING_DIR}/$(dirname "$d")"
    cp -R "$d" "${STAGING_DIR}/$(dirname "$d")/"
done

# 文件类
cp main.py "${STAGING_DIR}/main.py"
cp requirements.txt "${STAGING_DIR}/requirements.txt"
cp scripts/offline/install.bat "${STAGING_DIR}/install.bat"
cp scripts/offline/start.bat "${STAGING_DIR}/start.bat"
cp scripts/offline/upgrade.bat "${STAGING_DIR}/upgrade.bat"
cp README_OFFLINE.md "${STAGING_DIR}/README_OFFLINE.md"

# config 只带 example
mkdir -p "${STAGING_DIR}/config"
cp config/*.example.json "${STAGING_DIR}/config/" 2>/dev/null || true

# 清理 __pycache__
find "${STAGING_DIR}" -type d -name __pycache__ -prune -exec rm -rf {} + 2>/dev/null || true
find "${STAGING_DIR}" -type d -name .pytest_cache -prune -exec rm -rf {} + 2>/dev/null || true

# ---- 4. 写 BUILD_INFO.json ----
echo "[4/5] 写版本元数据..."
GIT_COMMIT="$(git rev-parse --short HEAD 2>/dev/null || echo unknown)"
BUILT_AT="$(date '+%Y-%m-%d %H:%M:%S')"
cat > "${STAGING_DIR}/BUILD_INFO.json" <<EOF
{
  "version": "${VERSION}",
  "built_at": "${BUILT_AT}",
  "git_commit": "${GIT_COMMIT}",
  "python_target": "3.12 / win_amd64",
  "built_on": "$(uname -s) $(uname -m) (cross-compiled)"
}
EOF

# ---- 5. 打 zip ----
echo "[5/5] 打 zip：${ZIP_PATH}"
[[ -f "${ZIP_PATH}" ]] && rm -f "${ZIP_PATH}"
( cd "${STAGING_DIR}" && zip -rq "${ZIP_PATH}" . )

# ---- 收尾 ----
ZIP_SIZE=$(du -h "${ZIP_PATH}" | awk '{print $1}')
WHEEL_COUNT=$(find "${WHEELS_DIR}" -maxdepth 1 -type f \( -name '*.whl' -o -name '*.tar.gz' -o -name '*.zip' \) 2>/dev/null | wc -l | tr -d ' ')
echo ""
echo "=== 完成 ==="
echo "Release 包：${ZIP_PATH} (${ZIP_SIZE})"
echo "Wheels 数量：${WHEEL_COUNT}"
echo "Staging：    ${STAGING_DIR}（可手工查内容）"
echo ""
echo "目标机器使用步骤："
echo "  1. 解压 zip"
echo "  2. 双击 install.bat（创建 venv，离线装 wheels）"
echo "  3. 双击 start.bat（启动 uvicorn，监听 8010）"
echo "  4. 浏览器打开 http://localhost:8010"
