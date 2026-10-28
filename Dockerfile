# syntax=docker/dockerfile:1

# ---------- frontend build ----------
FROM node:22-slim AS frontend-build
WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# ---------- backend runtime ----------
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    LLM_MODE=mock \
    CONTRACTGUARD_DATA_DIR=/app/backend/data \
    CONTRACTGUARD_FRONTEND_DIST=/app/frontend/dist

# onnxruntime / opencv / rapidocr runtime libraries.
# Use an Aliyun Debian mirror for users in mainland China; the default
# deb.debian.org endpoint is often unreachable or very slow there.
RUN if [ -f /etc/apt/sources.list.d/debian.sources ]; then \
        sed -i 's|deb.debian.org|mirrors.aliyun.com|g' /etc/apt/sources.list.d/debian.sources; \
    fi \
    && if [ -f /etc/apt/sources.list ]; then \
        sed -i 's|deb.debian.org|mirrors.aliyun.com|g' /etc/apt/sources.list; \
    fi \
    && apt-get update \
    && GLIB_PKG=$(apt-cache show libglib2.0-0t64 >/dev/null 2>&1 && echo libglib2.0-0t64 || echo libglib2.0-0) \
    && apt-get install -y --no-install-recommends libgomp1 libgl1 gosu "$GLIB_PKG" \
    && rm -rf /var/lib/apt/lists/*

# Install uv from PyPI (avoids a ghcr.io dependency in restricted networks).
RUN pip install --no-cache-dir \
    --index-url https://mirrors.aliyun.com/pypi/simple \
    uv==0.12.12

WORKDIR /app/backend
COPY backend/pyproject.toml backend/uv.lock backend/.python-version ./
RUN uv sync --frozen --no-dev

COPY backend/ ./
COPY --from=frontend-build /app/frontend/dist /app/frontend/dist

RUN useradd --create-home --uid 10001 appuser \
    && mkdir -p /app/data /app/backend/data \
    && chown -R appuser:appuser /app/data /app/backend/data

COPY docker-entrypoint.sh /usr/local/bin/contractguard-entrypoint
RUN chmod +x /usr/local/bin/contractguard-entrypoint

EXPOSE 8000
ENTRYPOINT ["/usr/local/bin/contractguard-entrypoint"]
CMD ["/app/backend/.venv/bin/uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
