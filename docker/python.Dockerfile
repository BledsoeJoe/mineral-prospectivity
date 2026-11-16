# Two targets from one file. `api` serves published artifacts with no model
# stack and no Dagster, small enough for the public node. `full` (the default,
# being last) adds them for the pipeline, Dagster, and the test suite. Built
# on top of `api`, so full is always a superset of what the API runs on.
FROM python:3.12-slim AS api

# curl feeds the api healthcheck.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src

RUN pip install --no-cache-dir ".[api]"

# Artifacts live on the shared volume or in object storage, never in the image.
ENV MPM_DATA_ROOT=/data

CMD ["uvicorn", "mpm.api:app", "--host", "0.0.0.0", "--port", "8000"]


FROM api AS full

# lightgbm needs OpenMP at runtime.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/*

COPY configs ./configs
COPY tests ./tests
COPY docker/dagster.yaml ./docker/dagster.yaml

RUN pip install --no-cache-dir ".[model,api,dev,orchestration]"

CMD ["python", "-m", "mpm", "etl", "run", "--profile", "synth", "--data-root", "/data"]
