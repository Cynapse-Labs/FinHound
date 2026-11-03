PYTHON ?= python3
all: ingest qc factors signal backtest
run:
	PYTHONPATH=services/core/src DATA_ROOT=data SETTINGS_PATH=config/settings.yaml $(PYTHON) -m core.pipeline all
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
	PYTHONPATH=services/core/src:services/ingest/src $(PYTHON) -m pytest -q
lint:
	$(PYTHON) -m ruff check services pyproject.toml && $(PYTHON) -m ruff format --check services pyproject.toml
