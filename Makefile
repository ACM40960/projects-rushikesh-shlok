.PHONY: setup test lint format precommit-install network experiment figures app reproduce clean

VENV := .venv
PY := $(VENV)/bin/python
PIP := $(VENV)/bin/pip

setup:
	python3 -m venv $(VENV)
	$(PIP) install --upgrade pip
	$(PIP) install -e ".[dev,ui,fleet]"
	$(VENV)/bin/pre-commit install

test:
	$(VENV)/bin/pytest

lint:
	$(VENV)/bin/ruff check .
	$(VENV)/bin/ruff format --check .

format:
	$(VENV)/bin/ruff format .
	$(VENV)/bin/ruff check --fix .

precommit-install:
	$(VENV)/bin/pre-commit install

network:
	$(VENV)/bin/dlm network build

experiment:
	$(VENV)/bin/dlm batch

figures:
	$(VENV)/bin/dlm figures

app:
	$(VENV)/bin/streamlit run app/main.py

# Regenerates every number and figure in the report, end to end, from a
# cold cache: the Dublin graph, the batch T1/T2/T3/Saving % experiment
# (docs/report/batch_results.csv), its figures, the service-time
# sensitivity sweep, and the hand-implemented-vs-OR-Tools benchmark.
# Each `dlm` subcommand builds/caches whatever graph or matrix it needs,
# so this is safe to run from a fresh clone.
reproduce:
	$(VENV)/bin/dlm network build
	$(VENV)/bin/dlm batch
	$(VENV)/bin/dlm figures
	$(VENV)/bin/dlm sensitivity
	$(VENV)/bin/dlm benchmark

clean:
	rm -rf $(VENV) .pytest_cache .ruff_cache **/__pycache__
