ARG PYTHON_IMAGE=python:3.12-slim-bookworm
FROM ${PYTHON_IMAGE}
ARG DEBIAN_MIRROR=https://mirrors.aliyun.com
ARG PYPI_INDEX=https://mirrors.cloud.tencent.com/pypi/simple/
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 UV_LINK_MODE=copy PATH="/app/backend/.venv/bin:$PATH"
RUN sed -i "s|http://deb.debian.org|${DEBIAN_MIRROR}|g; s|https://deb.debian.org|${DEBIAN_MIRROR}|g" /etc/apt/sources.list.d/debian.sources \
    && apt-get update && apt-get install -y --no-install-recommends libgomp1 libstdc++6 \
    && rm -rf /var/lib/apt/lists/*
RUN python -m pip install --no-cache-dir --index-url "${PYPI_INDEX}" uv==0.13.6
WORKDIR /app/backend
COPY backend/pyproject.toml backend/uv.lock ./
# 锁定版本与哈希，从指定镜像源下载；缓存保留到下次构建。
RUN --mount=type=cache,target=/root/.cache/uv \
    uv export --frozen --no-dev --no-emit-project --format requirements.txt --output-file /tmp/requirements.txt >/dev/null \
    && uv venv --python /usr/local/bin/python .venv \
    && uv pip install --python .venv/bin/python --require-hashes --default-index "${PYPI_INDEX}" -r /tmp/requirements.txt \
    && rm /tmp/requirements.txt
COPY backend/app ./app
COPY knowledge /app/knowledge
RUN mkdir -p /app/backend/data
EXPOSE 8020
# Milvus Lite 和 SQLite 适合单进程 demo，不开启多 worker。
CMD ["uvicorn","app.main:app","--host","0.0.0.0","--port","8020","--workers","1","--no-proxy-headers"]
