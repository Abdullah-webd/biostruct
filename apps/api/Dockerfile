# Biostruct API image. Also used to run the test suite on the compose network,
# which is how tests reach MongoDB without depending on host port forwarding.
FROM python:3.12-slim AS base

COPY --from=ghcr.io/astral-sh/uv:0.5.11 /uv /usr/local/bin/uv

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /app

# Dependency layer first, so source edits do not reinstall the world.
COPY pyproject.toml ./
COPY src ./src
RUN uv pip install --system -e .

# --- runtime image (no dev dependencies) ---------------------------------
FROM base AS runtime
# Run as a non-root user: a container that does not need root should not have it.
RUN useradd --create-home --uid 10001 app && chown -R app:app /app
USER app
EXPOSE 8000
# Railway supplies $PORT; default to 8000 for local runs.
CMD ["sh", "-c", "uvicorn biostruct.main:app --host 0.0.0.0 --port ${PORT:-8000}"]

# --- test image (adds dev dependencies and the tests) --------------------
FROM base AS test
RUN uv pip install --system -e ".[dev]"
COPY tests ./tests
CMD ["pytest"]
