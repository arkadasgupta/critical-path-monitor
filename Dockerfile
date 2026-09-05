FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

WORKDIR /app

# Run from source rather than installing the project: app/main.py locates
# static/ relative to __file__, which would resolve into site-packages if the
# package were installed.
ENV PYTHONPATH=/app \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

# Dependencies first, so edits to app/ or static/ do not invalidate this layer.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY app ./app
COPY static ./static

ENV PORT=8000
EXPOSE 8000

# Shell form so $PORT expands - Render assigns it at runtime.
CMD uv run --no-sync uvicorn app.main:app --host 0.0.0.0 --port ${PORT}
