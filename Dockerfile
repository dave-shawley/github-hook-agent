FROM python:3.14-slim AS builder

ENV \
	UV_COMPILE_BYTECODE=1 \
	UV_LINK_MODE=copy \
	UV_NATIVE_TLS=1 \
	UV_NO_MANAGED_PYTHON=1 \
	UV_PROJECT_ENVIRONMENT=/app \
	VIRTUAL_ENV=/app \
    UV_FROZEN=1 \
    UV_PYTHON_DOWNLOADS=never

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/
COPY . /source
RUN uv sync --directory /source --no-dev --no-default-groups --no-editable

FROM python:3.14-slim
EXPOSE 8000
ENV \
    PATH="/app/bin:$PATH" \
    UVICORN_HOST=0.0.0.0 \
    UVICORN_LOG_CONFIG="/app/log-config.json" \
    UVICORN_NO_SERVER_HEADER="true" \
    UVICORN_PROXY_HEADERS="true" \
    UVICORN_WORKERS="1" \
    UVICORN_WS="none" \
    VIRTUAL_ENV="/app"

COPY --from=builder /app /app
COPY /log-config.json /app/log-config.json
CMD ["/app/bin/uvicorn", "--factory", "github_hook_agent.entrypoint:create_app"]
