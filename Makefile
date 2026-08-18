.PHONY: install synth run test lint all clean etl-synth etl-us-cu up up-real down docker-test

install:
	pip install -e ".[dev,api]"

synth:
	python -m mpm synth --out data/cube.parquet

run:
	python -m mpm run --cube data/cube.parquet --config configs/default.yaml --out results

etl-synth:
	python -m mpm etl run --profile synth --data-root data

etl-us-cu:
	python -m mpm etl run --profile us-cu --data-root data

test:
	pytest -q

lint:
	ruff check src tests

all: synth run

# ---- docker compose stack ----------------------------------------------

up:                 ## synth pipeline + api + web on :8080
	docker compose up --build -d

up-real:            ## real-data US copper cube
	MPM_PROFILE=us-cu docker compose up --build -d

down:
	docker compose down

docker-test:
	docker compose run --rm pipeline sh -c "pytest -q && ruff check src tests"

clean:
	rm -rf results .pytest_cache **/__pycache__
