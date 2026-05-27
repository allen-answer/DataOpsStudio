#!/usr/bin/env bash
# Wave 5 #20:在标准 Python 3.12 Linux 环境跑 pip-compile,生成
# `requirements.lock.txt`(含 SHA256 hash)。
#
# 为什么用 Docker 而不是本地 pip-compile:
# - Windows 本地 Python 跟 Linux 容器 wheels 不同(numpy / pyarrow 等)
# - pip-compile --generate-hashes 锁定的是具体 wheel SHA,跨平台不通用
# - CI / Dockerfile 用 Linux,lock 文件必须从 Linux 生成
#
# 用法:
#   bash scripts/regenerate-lock.sh
#
# 产出:
#   - requirements.lock.txt(覆盖原文件)
#
# Dependabot 升级流程:
#   1. Dependabot 给 requirements.in 提 PR 改版本
#   2. 维护者本地跑 `bash scripts/regenerate-lock.sh` 重 lock + 提交
#   3. CI / Docker 用新 lock 跑回归
set -euo pipefail

cd "$(dirname "$0")/.."

IMAGE="python:3.12-slim"
WORKDIR=/work

echo "[regen-lock] running pip-compile inside $IMAGE ..."

docker run --rm -v "$PWD:$WORKDIR" -w "$WORKDIR" "$IMAGE" bash -c "
  pip install --quiet --no-cache-dir pip-tools && \
  pip-compile --generate-hashes --strip-extras \
    --output-file requirements.lock.txt \
    requirements.in
"

echo "[regen-lock] done. Inspect git diff requirements.lock.txt"
