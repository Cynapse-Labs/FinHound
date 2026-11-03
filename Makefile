PYTHON ?= python3
all: ingest qc factors signal backtest
build:
	docker compose build
up:
	docker compose up --build
ingest:
	mkdir -p data/raw/prices data/raw/actions
	PYTHONPATH=services/ingest/src UNIVERSE_PATH=config/universe.yaml DATA_ROOT=data $(PYTHON) -m ingest
qc:
	PYTHONPATH=services/core/src DATA_ROOT=data $(PYTHON) -m core.pipeline qc
factors:
	PYTHONPATH=services/core/src DATA_ROOT=data $(PYTHON) -m core.pipeline factors
signal:
	PYTHONPATH=services/core/src DATA_ROOT=data SETTINGS_PATH=config/settings.yaml $(PYTHON) -m core.pipeline signal
backtest:
	PYTHONPATH=services/core/src DATA_ROOT=data SETTINGS_PATH=config/settings.yaml $(PYTHON) -m core.pipeline backtest
test:
	pytest -q
lint:
	ruff check . && ruff format --check .
