FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

# Keep the venv outside /app so a bind-mounted repo doesn't shadow it
ENV UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_LINK_MODE=copy \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY pyproject.toml uv.lock README.md ./
COPY src ./src
RUN uv sync --frozen --no-dev

EXPOSE 8090

CMD ["uv", "run", "copilot", "serve", "--host", "0.0.0.0", "--port", "8090"]
