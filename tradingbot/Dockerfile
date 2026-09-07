# tradingbot — runnable container image.
FROM python:3.11-slim AS base

# No bytecode files, unbuffered logs.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Install dependencies first (better layer caching).
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir .

# Default (non-secret) config. Override by mounting your own config.yaml.
COPY config.yaml ./config.yaml

# Persisted at runtime via volumes (see docker-compose.yml):
#   /app/data    -> SQLite trade log + counters
#   /app/reports -> weekly report files
RUN mkdir -p /app/data /app/reports

# Run as a non-root user.
RUN useradd --create-home --uid 10001 bot && chown -R bot:bot /app
USER bot

ENTRYPOINT ["python", "-m", "tradingbot"]
CMD ["run"]
