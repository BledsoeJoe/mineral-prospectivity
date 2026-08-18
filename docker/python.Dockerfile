# Shared image for the pipeline and the API: same code, different command.
FROM python:3.12-slim

# lightgbm needs OpenMP at runtime; curl feeds the api healthcheck.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgomp1 curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src
COPY configs ./configs
COPY tests ./tests
COPY docker/dagster.yaml ./docker/dagster.yaml

RUN pip install --no-cache-dir ".[api,dev,orchestration]"

# Artifacts live on the shared volume, never in the image.
ENV MPM_DATA_ROOT=/data

CMD ["python", "-m", "mpm", "etl", "run", "--profile", "synth", "--data-root", "/data"]
