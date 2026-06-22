# Hermes VM Shadow Deploy image — Phase 11.
#
# Shadow mode only. This image installs runtime dependencies and deterministic
# application code. It contains NO broker SDK, NO credentials, and NO live data
# secrets. Submission is impossible by configuration (BROKER_MODE=none) and by
# the shadow service having no submit path.
FROM python:3.13-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# Runtime dependencies only — explicitly NOT the dev tooling (pytest/ruff/pyright)
# and NOT any broker/network SDK.
RUN pip install --no-cache-dir "pydantic>=2.7,<3" "tzdata>=2024.1"

# Deterministic application packages.
COPY pyproject.toml ./
COPY config ./config
COPY services ./services
COPY ops ./ops
COPY gateway ./gateway
COPY schemas ./schemas
COPY storage ./storage
COPY data ./data
COPY strategies ./strategies
COPY replay ./replay
COPY brokers ./brokers
COPY infra/deploy ./infra/deploy

# Non-root runtime user. The /opt/hermes data/log directories are provided by
# mounted volumes at runtime and owned by the host deploy user (uid 1000).
RUN useradd --create-home --uid 1000 hermes
USER hermes

# Default: run a single deterministic shadow cycle. Compose overrides this with
# the loop wrappers for the long-running services.
CMD ["python", "-m", "services"]
