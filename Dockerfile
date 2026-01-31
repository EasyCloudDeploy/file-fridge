# --- Stage 1: Build stage ---
FROM python:3.12-slim-bookworm AS builder

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

# Install dependencies first (for better caching)
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --frozen --no-install-project --no-dev


COPY . .

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev


# --- Stage 2: Runtime stage ---
FROM python:3.12-slim-bookworm

# Create a non-privileged user for security
RUN groupadd -r filefridge && useradd -r -g filefridge filefridge

WORKDIR /app

# Copy the virtual environment from the builder
COPY --from=builder --chown=filefridge:filefridge /app/.venv /app/.venv

COPY --from=builder --chown=filefridge:filefridge /app /app

# Create data directory with proper permissions
RUN mkdir -p /app/data && chown -R filefridge:filefridge /app/data

# Set environment variables
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1

USER filefridge

EXPOSE 8000

# Use uvicorn directly for better production deployment
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port 8000 --log-level ${LOG_LEVEL:-info}"]