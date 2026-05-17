#!/usr/bin/env bash
# 装 scripts/git-hooks/ 下的所有 hook 到 .git/hooks/。
# clone 仓库后跑一次：bash scripts/install-git-hooks.sh
# POSIX shell 通用（Linux / macOS / Windows Git Bash / WSL）。

set -eu
ROOT="$(git rev-parse --show-toplevel)"
SRC="$ROOT/scripts/git-hooks"
DST="$ROOT/.git/hooks"

mkdir -p "$DST"

for hook in "$SRC"/*; do
  name="$(basename "$hook")"
  cp "$hook" "$DST/$name"
  chmod +x "$DST/$name"
  echo "installed: $DST/$name"
done

echo ""
echo "完成。改 hook 后重新跑这个脚本。"
