SHELL := /bin/bash

PYTHON_VERSION ?= 3.11
CORE_VENV ?= .venv-core
INGEST_VENV ?= .venv-ingest

CORE_PYTHON ?= $(CORE_VENV)/bin/python
INGEST_PYTHON ?= $(INGEST_VENV)/bin/python

.PHONY: all setup qlib universe local build up api ingest qc factors signal directions backtest optimize test lint

all: ingest qc factors signal directions backtest

setup:
	PYTHON_VERSION=$(PYTHON_VERSION) CORE_VENV=$(CORE_VENV) INGEST_VENV=$(INGEST_VENV) WITH_QLIB=$(WITH_QLIB) ./scripts/setup_env.sh

qlib:
	WITH_QLIB=1 PYTHON_VERSION=$(PYTHON_VERSION) CORE_VENV=$(CORE_VENV) INGEST_VENV=$(INGEST_VENV) ./scripts/setup_env.sh

universe:
	$(CORE_PYTHON) scripts/build_universe.py --output config/universe.yaml

local: setup
	PYTHONPATH=services/ingest/src UNIVERSE_PATH=config/universe.yaml DATA_ROOT=data $(INGEST_PYTHON) -m ingest
	PYTHONPATH=services/core/src DATA_ROOT=data UNIVERSE_PATH=config/universe.yaml SETTINGS_PATH=config/settings.yaml $(CORE_PYTHON) -m core.pipeline all
	PYTHONPATH=services/core/src DATA_ROOT=data $(CORE_PYTHON) -m uvicorn core.api:app --host 127.0.0.1 --port 8000

build:
	docker compose build

up:
	docker compose up --build

api:
	PYTHONPATH=services/core/src DATA_ROOT=data UNIVERSE_PATH=config/universe.yaml SETTINGS_PATH=config/settings.yaml $(CORE_PYTHON) -m uvicorn core.api:app --host 127.0.0.1 --port 8000

ingest:
	mkdir -p data/raw/prices data/raw/actions
	PYTHONPATH=services/ingest/src UNIVERSE_PATH=config/universe.yaml DATA_ROOT=data $(INGEST_PYTHON) -m ingest

qc:
	PYTHONPATH=services/core/src DATA_ROOT=data UNIVERSE_PATH=config/universe.yaml $(CORE_PYTHON) -m core.pipeline qc

factors:
	PYTHONPATH=services/core/src DATA_ROOT=data UNIVERSE_PATH=config/universe.yaml $(CORE_PYTHON) -m core.pipeline factors

signal:
	PYTHONPATH=services/core/src DATA_ROOT=data UNIVERSE_PATH=config/universe.yaml SETTINGS_PATH=config/settings.yaml $(CORE_PYTHON) -m core.pipeline signal

directions:
	PYTHONPATH=services/core/src DATA_ROOT=data UNIVERSE_PATH=config/universe.yaml SETTINGS_PATH=config/settings.yaml $(CORE_PYTHON) -m core.pipeline directions

backtest:
	PYTHONPATH=services/core/src DATA_ROOT=data UNIVERSE_PATH=config/universe.yaml SETTINGS_PATH=config/settings.yaml $(CORE_PYTHON) -m core.pipeline backtest

optimize:
	PYTHONPATH=services/core/src DATA_ROOT=data UNIVERSE_PATH=config/universe.yaml $(CORE_PYTHON) -m core.pipeline optimize

test:
	PYTHONPATH=services/core/src:services/ingest/src $(CORE_PYTHON) -m pytest -q

lint:
	$(CORE_PYTHON) -m ruff check services pyproject.toml && $(CORE_PYTHON) -m ruff format --check services pyproject.toml
