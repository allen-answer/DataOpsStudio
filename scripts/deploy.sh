#!/usr/bin/env bash
#
# deploy.sh — 把 DataOpsStudio 从本地一键部署到云端服务器
#
# 流程:
#   1. 校验本地 git working tree 干净
#   2. (可选)git push 到 GitHub —— 默认开,可 --skip-push
#   3. rsync 源码到云端 working dir(按 .gitignore 自动排除 runtime 数据)
#   4. 同步云端 git ref(走 bundle —— 云端无 GitHub 凭证)
#   5. ssh 触发 docker compose up -d --build
#   6. 验证容器健康 + /api/sql-workbench/format endpoint 可达
#
# 配置(环境变量覆盖):
#   DEPLOY_HOST    云端 host(默认 110.42.230.97)
#   DEPLOY_USER    云端 user(默认 ubuntu)
#   DEPLOY_KEY     SSH 私钥路径(默认 /Users/answer/myproject/connect_key.pem)
#   DEPLOY_PATH    云端项目路径(默认 /home/ubuntu/dataops-studio)
#   DEPLOY_BRANCH  本地分支(默认 main)
#
# 用法:
#   bash scripts/deploy.sh                # 全套
#   bash scripts/deploy.sh --skip-push    # 已 push 过,跳过步骤 2
#   bash scripts/deploy.sh --skip-build   # 只 rsync + git ref,不动 docker
#   bash scripts/deploy.sh --dry-run      # rsync -n 试运行,不真改云端
#   bash scripts/deploy.sh -h             # 帮助

set -euo pipefail

DEPLOY_HOST="${DEPLOY_HOST:-110.42.230.97}"
DEPLOY_USER="${DEPLOY_USER:-ubuntu}"
DEPLOY_KEY="${DEPLOY_KEY:-/Users/answer/myproject/connect_key.pem}"
DEPLOY_PATH="${DEPLOY_PATH:-/home/ubuntu/dataops-studio}"
DEPLOY_BRANCH="${DEPLOY_BRANCH:-main}"

SKIP_PUSH=false
SKIP_BUILD=false
DRY_RUN=false

for arg in "$@"; do
  case "$arg" in
    --skip-push)  SKIP_PUSH=true ;;
    --skip-build) SKIP_BUILD=true ;;
    --dry-run)    DRY_RUN=true ;;
    -h|--help)
      sed -n '2,28p' "$0"
      exit 0
      ;;
    *)
      echo "未知参数: $arg(--help 看用法)" >&2
      exit 2
      ;;
  esac
done

# ANSI 颜色
GREEN='\033[0;32m'; RED='\033[0;31m'; YELLOW='\033[0;33m'; BLUE='\033[0;34m'; NC='\033[0m'
step() { echo -e "\n${BLUE}━━ $1${NC}"; }
ok()   { echo -e "${GREEN}✓${NC} $1"; }
warn() { echo -e "${YELLOW}⚠${NC} $1"; }
die()  { echo -e "${RED}✗${NC} $1" >&2; exit 1; }

SSH_CMD=(ssh -i "$DEPLOY_KEY" -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10)
SSH_TARGET="$DEPLOY_USER@$DEPLOY_HOST"

# 脚本本身在 scripts/,repo root 在它上一层
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

# ── 0. 前置体检 ────────────────────────────────────────────────────────
[[ -f "$DEPLOY_KEY" ]] || die "私钥不存在: $DEPLOY_KEY"
[[ -d ".git" ]] || die "$REPO_ROOT 不是 git 仓库"
command -v rsync >/dev/null || die "rsync 未安装"

# ── 1. 校验本地 git 状态 ──────────────────────────────────────────────
step "1/6 校验本地 git 状态"
if [[ -n "$(git status --porcelain)" ]]; then
  git status -s
  die "本地 working tree 有未提交改动,请先 commit 或 stash"
fi
LOCAL_HEAD=$(git rev-parse HEAD)
LOCAL_BRANCH=$(git rev-parse --abbrev-ref HEAD)
ok "branch=$LOCAL_BRANCH HEAD=${LOCAL_HEAD:0:9}"
if [[ "$LOCAL_BRANCH" != "$DEPLOY_BRANCH" ]]; then
  warn "当前在 $LOCAL_BRANCH(配置部署分支是 $DEPLOY_BRANCH),3 秒后继续,Ctrl+C 中止"
  sleep 3
fi

# ── 2. push 到 GitHub ─────────────────────────────────────────────────
if $SKIP_PUSH; then
  step "2/6 push 到 GitHub  [跳过 --skip-push]"
else
  step "2/6 push 到 GitHub"
  REMOTE_HEAD=$(git rev-parse "origin/$LOCAL_BRANCH" 2>/dev/null || echo "")
  if [[ "$REMOTE_HEAD" == "$LOCAL_HEAD" ]]; then
    ok "origin/$LOCAL_BRANCH 已是 ${LOCAL_HEAD:0:9}"
  elif $DRY_RUN; then
    echo "[dry-run] git push origin $LOCAL_BRANCH"
  else
    git push origin "$LOCAL_BRANCH"
    ok "推送到 origin/$LOCAL_BRANCH"
  fi
fi

# ── 3. rsync 源码到云端 ───────────────────────────────────────────────
step "3/6 rsync 源码到云端"
# .gitignore 自动覆盖 logs/ results/ config/*.json static/spa/ node_modules/ 等
# runtime 数据;再显式排掉 .git/(走 bundle 同步)和几个明确的 ad-hoc 路径。
RSYNC_FLAGS=(
  -avz
  --filter='dir-merge,- .gitignore'
  --exclude='.git/'
  --exclude='.DS_Store'
  --exclude='*.bundle'
  --exclude='.openapi.json'
  --exclude='__pycache__/'
  # 云端 .env(secret 配置)绝不能被本地空 .env / 不存在的文件覆盖
  --exclude='.env'
)
$DRY_RUN && RSYNC_FLAGS+=(--dry-run)

rsync "${RSYNC_FLAGS[@]}" \
  -e "ssh -i $DEPLOY_KEY -o StrictHostKeyChecking=accept-new" \
  ./ "$SSH_TARGET:$DEPLOY_PATH/" | tail -12
ok "源码已同步到 $SSH_TARGET:$DEPLOY_PATH"

# ── 4. 同步云端 git ref(bundle) ──────────────────────────────────────
step "4/6 同步云端 git ref"
REMOTE_HEAD=$("${SSH_CMD[@]}" "$SSH_TARGET" "cd $DEPLOY_PATH && git rev-parse HEAD")
echo "云端 HEAD: ${REMOTE_HEAD:0:9}"
echo "本地 HEAD: ${LOCAL_HEAD:0:9}"

if [[ "$REMOTE_HEAD" == "$LOCAL_HEAD" ]]; then
  ok "云端 git HEAD 已对齐(跳过 bundle)"
elif $DRY_RUN; then
  echo "[dry-run] 会生成 bundle 并 fetch + reset --hard 到 ${LOCAL_HEAD:0:9}"
else
  BUNDLE=$(mktemp -t dataops-deploy.XXXXXX.bundle)
  if ! git bundle create "$BUNDLE" "$LOCAL_BRANCH" --not "$REMOTE_HEAD" 2>/dev/null; then
    warn "增量 bundle 失败(云端 ${REMOTE_HEAD:0:9} 在本地不可达?),回退全量 bundle"
    git bundle create "$BUNDLE" "$LOCAL_BRANCH"
  fi
  BUNDLE_SIZE=$(wc -c < "$BUNDLE" | tr -d ' ')
  echo "bundle: $BUNDLE ($BUNDLE_SIZE bytes)"

  REMOTE_BUNDLE="/tmp/$(basename "$BUNDLE")"
  scp -i "$DEPLOY_KEY" "$BUNDLE" "$SSH_TARGET:$REMOTE_BUNDLE" >/dev/null
  "${SSH_CMD[@]}" "$SSH_TARGET" "
    set -e
    cd '$DEPLOY_PATH'
    git fetch '$REMOTE_BUNDLE' '$LOCAL_BRANCH:_deploy_incoming' 2>&1 | tail -3
    git reset --hard '$LOCAL_HEAD'
    git update-ref refs/remotes/origin/'$LOCAL_BRANCH' '$LOCAL_HEAD'
    git update-ref -d refs/heads/_deploy_incoming 2>/dev/null || true
    rm '$REMOTE_BUNDLE'
  "
  rm "$BUNDLE"
  ok "云端 git HEAD 对齐到 ${LOCAL_HEAD:0:9}"
fi

# ── 5. docker rebuild ────────────────────────────────────────────────
if $SKIP_BUILD; then
  step "5/6 docker rebuild  [跳过 --skip-build]"
elif $DRY_RUN; then
  step "5/6 docker rebuild  [dry-run 跳过]"
else
  step "5/6 docker compose up -d --build"
  "${SSH_CMD[@]}" "$SSH_TARGET" "cd $DEPLOY_PATH && docker compose up -d --build" 2>&1 | tail -10
  ok "镜像已重建,容器已重启"
fi

# ── 6. 验证 ──────────────────────────────────────────────────────────
if $DRY_RUN || $SKIP_BUILD; then
  step "6/6 验证  [跳过]"
else
  step "6/6 验证"
  sleep 3
  HEALTH=$("${SSH_CMD[@]}" "$SSH_TARGET" "docker ps --filter name=dataops-studio --format '{{.Status}}'")
  echo "container: $HEALTH"
  [[ "$HEALTH" == *Up* ]] || die "容器未启动,ssh 进去看 docker logs dataops-studio"

  FORMAT_CHECK=$("${SSH_CMD[@]}" "$SSH_TARGET" "curl -sI http://localhost:8010/api/sql-workbench/format" | head -1 | tr -d '\r')
  echo "endpoint: $FORMAT_CHECK"
  [[ "$FORMAT_CHECK" == *405* ]] || die "/api/sql-workbench/format 不可达,ssh 排查"
  ok "endpoint 正常(405 = GET 不允许,POST 路由就绪)"
fi

echo
echo -e "${GREEN}📦 部署的 commit: ${LOCAL_HEAD:0:9}$(git log -1 --format=' (%s)' | cut -c 1-80)${NC}"
echo -e "${GREEN}🌐 访问: http://$DEPLOY_HOST/  (nginx)  或  http://$DEPLOY_HOST:8010/  (直连)${NC}"
