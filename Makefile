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

# --- Stages 1+ (not yet implemented) -----------------------------------

network:
	@echo "dlm network build: lands in Stage 1 (docs/stages/stage-01-network.md)"; exit 1

experiment:
	$(VENV)/bin/dlm batch

figures:
	$(VENV)/bin/dlm figures

app:
	@echo "make app: lands in Stage 10 (docs/stages/stage-10-ui.md)"; exit 1

reproduce:
	@echo "make reproduce: lands in Stage 9 (docs/stages/stage-09-hardening.md)"; exit 1

clean:
	rm -rf $(VENV) .pytest_cache .ruff_cache **/__pycache__
