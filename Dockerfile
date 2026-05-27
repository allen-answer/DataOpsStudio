# 多阶段构建：node 阶段产 static/spa/，python 阶段直接拷过来。
# 不再依赖宿主机预先 npm run build —— `docker compose up -d --build`
# 一键就能跑出完整可访问的产品。

FROM node:20-alpine AS frontend
WORKDIR /src

# 利用 docker layer cache：先只拷 package.json + lock，装依赖；再拷源码 build。
# 改源码不会触发 npm ci，节省构建时间。
COPY frontend/frontend/package.json frontend/frontend/package-lock.json* frontend/frontend/
RUN cd frontend/frontend && npm ci --no-audit --no-fund

# vite.config.js 写死 outDir = '../../static/spa'，相对 frontend/frontend
# 即解析为 /src/static/spa。下个阶段从这里 COPY 出来。
COPY frontend/frontend/ frontend/frontend/
RUN cd frontend/frontend && npm run build


FROM python:3.12-slim AS python-builder
WORKDIR /app
# 用 aliyun mirror（腾讯云内 mirror 在并发下载下偶发连接重置 → pip 解析空 versions
# 列表 → "No matching distribution" 假阳性）。--retries / --timeout 兜底瞬时抖动。
ENV PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/ \
    PIP_TRUSTED_HOST=mirrors.aliyun.com \
    PIP_DEFAULT_TIMEOUT=120 \
    PIP_RETRIES=5
# Wave 5 #20:优先用 lock 文件(可复现);未入仓时回退 requirements.txt
COPY requirements.txt requirements.in* requirements.lock.txt* ./
RUN if [ -f requirements.lock.txt ]; then \
      pip wheel --no-cache-dir -r requirements.lock.txt -w /wheels; \
    else \
      pip wheel --no-cache-dir -r requirements.txt -w /wheels; \
    fi


FROM python:3.12-slim
WORKDIR /app
ENV LD_LIBRARY_PATH="/usr/local/lib/python3.12/site-packages/dmssl:/usr/local/lib/python3.12/site-packages/dmpython.libs:${LD_LIBRARY_PATH}"

COPY --from=python-builder /wheels /wheels
COPY requirements.txt requirements.in* requirements.lock.txt* ./
RUN if [ -f requirements.lock.txt ]; then \
      pip install --no-cache-dir --no-index --find-links=/wheels -r requirements.lock.txt; \
    else \
      pip install --no-cache-dir --no-index --find-links=/wheels -r requirements.txt; \
    fi && rm -rf /wheels

# 拷后端源码，再用前端 build 产物覆盖 static/spa（确保不会带上宿主机的 dirty 产物）。
COPY . .
COPY --from=frontend /src/static/spa /app/static/spa

# #18:非 root 运行 —— 降低容器逃逸面 + 误写宿主机 volume 风险。
# uid 1000 是 Linux 常见首个非 root 用户,跟 host volume 拥有者通常一致。
# /app 整个目录 chown 让运行时写 config/results/logs 等有权限。
RUN groupadd --system --gid 1000 dataops \
    && useradd --system --uid 1000 --gid dataops --home-dir /app --shell /sbin/nologin dataops \
    && mkdir -p /app/config /app/results /app/logs \
    && chown -R dataops:dataops /app

USER dataops

EXPOSE 8010
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8010"]
