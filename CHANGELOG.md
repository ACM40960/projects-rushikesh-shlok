# Changelog

One section per stage. Dates are UTC.

## Stage 0 — Foundations (2026-08-15)

- Repository skeleton created per `docs/architecture.md` / project brief §4:
  `src/dlm/` package (network, instance, solver, disruption, simulation, viz,
  config, cli), `app/` thin-client stub, `scenarios/`, `data/`, `results/`,
  `tests/`, `notebooks/`, `docs/` (index, architecture, glossary, ADRs,
  per-stage write-ups).
- `pyproject.toml` + pinned `requirements.txt` lockfile; `Makefile` with
  `setup` / `test` / `lint` / `format` targets (later-stage targets present
  but stubbed with a clear "lands in Stage N" message).
- `src/dlm/config.py`: pydantic-settings-based configuration (paths, seed,
  log level, units policy), overridable via `DLM_*` env vars or `.env`.
- `src/dlm/logging_conf.py`: structured logging setup.
- `src/dlm/cli.py`: Typer entry point (`dlm --version`); domain sub-commands
  land stage by stage.
- Pre-commit (ruff + ruff-format) and GitHub Actions CI (lint + test) wired up.
- One trivial-but-real smoke test suite (`tests/test_foundations.py`), plus
  placeholder test files for each future stage's module.
- ADR-0001: fixed technical stack, recorded as a decision record rather than
  re-litigated.
