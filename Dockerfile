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
COPY requirements.txt .
RUN pip wheel --no-cache-dir -r requirements.txt -w /wheels


FROM python:3.12-slim
WORKDIR /app
ENV LD_LIBRARY_PATH="/usr/local/lib/python3.12/site-packages/dmssl:/usr/local/lib/python3.12/site-packages/dmpython.libs:${LD_LIBRARY_PATH}"

COPY --from=python-builder /wheels /wheels
COPY requirements.txt .
RUN pip install --no-cache-dir --no-index --find-links=/wheels -r requirements.txt && rm -rf /wheels

# 拷后端源码，再用前端 build 产物覆盖 static/spa（确保不会带上宿主机的 dirty 产物）。
COPY . .
COPY --from=frontend /src/static/spa /app/static/spa

EXPOSE 8010
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8010"]
