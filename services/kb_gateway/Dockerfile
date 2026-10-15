FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:0.11 /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY src ./src
RUN uv sync --frozen --no-dev
USER nobody
EXPOSE 8080
CMD ["/app/.venv/bin/uvicorn", "kb_gateway.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8080"]
