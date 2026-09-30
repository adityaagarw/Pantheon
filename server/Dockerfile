# Pantheon backend (FastAPI + LangGraph agent runtime).
# Build from the repo root: docker build -f server/Dockerfile .
FROM python:3.12-slim

# Tooling agents commonly need in their workspaces (shell tool) and runtimes
# for stdio MCP servers (npx / uvx).
RUN apt-get update \
    && apt-get install -y --no-install-recommends git curl ca-certificates ripgrep nodejs npm \
    && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir uv

WORKDIR /app
COPY server/pyproject.toml server/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
# Headless Chromium for the agents' built-in browser and Stage checks.
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
RUN /app/.venv/bin/playwright install --with-deps chromium && rm -rf /var/lib/apt/lists/*
COPY server/ .

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PANTHEON_DATA_DIR=/data \
    PANTHEON_FERNET_KEY_PATH=/data/secrets/pantheon.key \
    PANTHEON_WORKSPACE_ROOTS=/workspaces

EXPOSE 8710
# Migrations run on startup (PANTHEON_AUTO_MIGRATE=true).
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8710"]
