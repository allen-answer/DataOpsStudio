# Standalone e2e image —— 不要进主 dataops-studio image 里。
# chromium + 系统字体 + playwright 大概 +400MB，prod 不需要。
#
# 跑法（从仓库根目录）：
#   1. 先把 app 起来：docker compose up -d
#   2. 跑 e2e profile：docker compose --profile e2e run --rm e2e
# 或独立 build/run（不依赖 compose）：
#   docker build -t dataops-e2e -f tests/e2e/Dockerfile .
#   docker run --rm --network=host -e E2E_BASE_URL=http://localhost:8010 dataops-e2e
FROM python:3.12-slim

# 系统包：chromium 运行时 + 字体（中文渲染需要）
RUN apt-get update && apt-get install -y --no-install-recommends \
    libnss3 libatk1.0-0 libatk-bridge2.0-0 libcups2 libdrm2 libdbus-1-3 \
    libxkbcommon0 libxcomposite1 libxdamage1 libxfixes3 libxrandr2 \
    libgbm1 libpango-1.0-0 libcairo2 libasound2 libatspi2.0-0 \
    fonts-liberation fonts-noto-cjk \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY tests/e2e/requirements-e2e.txt /app/tests/e2e/requirements-e2e.txt
RUN pip install --no-cache-dir -r tests/e2e/requirements-e2e.txt \
    && playwright install chromium

# 仅拷测试相关代码 —— app/ 不需要进 e2e image，e2e 只是 client
COPY tests/ tests/

ENV E2E_BASE_URL=http://app:8010 \
    PYTHONUNBUFFERED=1

CMD ["pytest", "tests/e2e/", "-v"]
