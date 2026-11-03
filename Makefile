CORE_PYTHON ?= .venv-core/bin/python
INGEST_PYTHON ?= .venv-ingest/bin/python

all: ingest qc factors signal backtest

setup:
	python3 -m venv .venv-core
	python3 -m venv .venv-ingest
	.venv-core/bin/pip install -q -r services/core/requirements.txt
	.venv-ingest/bin/pip install -q -r services/ingest/requirements.txt

local: setup
	PYTHONPATH=services/ingest/src UNIVERSE_PATH=config/universe.yaml DATA_ROOT=data $(INGEST_PYTHON) -m ingest
	PYTHONPATH=services/core/src DATA_ROOT=data SETTINGS_PATH=config/settings.yaml $(CORE_PYTHON) -m core.pipeline all
	PYTHONPATH=services/core/src DATA_ROOT=data $(CORE_PYTHON) -m uvicorn core.api:app --host 127.0.0.1 --port 8000

build:
	docker compose build

up:
	docker compose up --build

ingest:
	mkdir -p data/raw/prices data/raw/actions
	PYTHONPATH=services/ingest/src UNIVERSE_PATH=config/universe.yaml DATA_ROOT=data $(INGEST_PYTHON) -m ingest

qc:
	PYTHONPATH=services/core/src DATA_ROOT=data $(CORE_PYTHON) -m core.pipeline qc

factors:
	PYTHONPATH=services/core/src DATA_ROOT=data $(CORE_PYTHON) -m core.pipeline factors

signal:
	PYTHONPATH=services/core/src DATA_ROOT=data SETTINGS_PATH=config/settings.yaml $(CORE_PYTHON) -m core.pipeline signal

backtest:
	PYTHONPATH=services/core/src DATA_ROOT=data SETTINGS_PATH=config/settings.yaml $(CORE_PYTHON) -m core.pipeline backtest

test:
	PYTHONPATH=services/core/src:services/ingest/src $(CORE_PYTHON) -m pytest -q

lint:
	$(CORE_PYTHON) -m ruff check services pyproject.toml && $(CORE_PYTHON) -m ruff format --check services pyproject.toml
