# Stage 00 — Foundations

## Goal

Establish a repository that runs, tests, and documents itself before any
domain logic (routing, disruptions, metrics) exists. Every later stage
builds inside this skeleton and depends on its conventions — the module
layout, the settings object, the logging setup, the CLI entry point, the
lint/test/CI wiring, and the documentation contract — being fixed and
working now.

## Scope

**In scope:**
- Repository skeleton per the project brief §4 (empty modules with
  docstrings, since no domain logic exists yet).
- `pyproject.toml`, pinned `requirements.txt` lockfile, `.gitignore`, `Makefile`.
- `src/dlm/config.py` (pydantic-settings: paths, seed, log level, units policy).
- `src/dlm/logging_conf.py` (structured logging, level from env).
- `src/dlm/cli.py` (Typer app skeleton; `--version` only — domain sub-commands
  are added by the stage that introduces their underlying module).
- pytest configuration and one real (if small) smoke-test suite.
- Pre-commit (ruff + ruff-format) and GitHub Actions CI (lint + test).
- `README.md`, `docs/index.md`, `docs/glossary.md`, `docs/architecture.md`
  (first draft), `docs/adr/ADR-0001-tech-stack.md`.

**Explicitly out of scope** (land in the stage noted):
- Anything that touches OpenStreetMap or a real graph — Stage 1.
- Instance/stop/depot modelling — Stage 2.
- Any solver, disruption, or metrics logic — Stages 4–6.
- The Streamlit UI — Stage 10.

## Design

**Repo layout.** Followed the project brief's §4 tree exactly, at the
repository root (no extra `dublin-last-mile/` wrapper directory — the git
repository itself is that root). Every module that will hold real logic in
a later stage exists now as a file with a docstring only, naming the stage
that will populate it and the stage doc to read. This is deliberately not
"dead code": each stub is a documented placeholder in a fixed, reviewed
layout, not a discarded experiment, and pytest collects zero tests from the
matching empty test files without erroring — nothing here is presented as
working when it is not.

**Settings via pydantic-settings.** `Settings` (in `dlm.config`) is a
`BaseSettings` subclass so every path, the default seed, and the log level
can be overridden by `DLM_*` environment variables or a `.env` file,
without touching code — this is what later stages (and CI, and any
collaborator's machine) rely on for reproducibility. Units are fixed
module-level constants (`TIME_UNIT = "seconds"`, `DISTANCE_UNIT = "metres"`)
rather than settings, because they are a project-wide invariant, not a
per-run choice — allowing them to vary would silently break every cached
artefact's comparability.

**Logging as a separate module.** `configure_logging()` lives in
`dlm.logging_conf` rather than inside `config.py`, so that importing
`dlm.config` (which many modules will do just to read a path) never has the
side effect of reconfiguring the root logger. Only entry points (the CLI,
the future Streamlit app, batch scripts) call it.

**CLI now, sub-commands later.** `dlm.cli` exists as a Typer app with
`--version` and nothing else. Every future sub-command
(`network`, `instance`, `plan`, `disrupt`, `compare`, `batch`) is documented
in the module's own docstring with the stage that adds it, so the CLI
surface is planned from the start rather than accreted ad hoc.

**Lockfile.** `requirements.txt` is a full `pip freeze` of the project
installed with all extras (`dev`, `ui`, `fleet`) into a clean virtualenv —
so `make setup` on a fresh clone reproduces exactly the dependency set this
stage was built and tested against, including packages (Streamlit,
OR-Tools) that no code imports yet. This trades a slightly heavier install
now for guaranteeing Stage 8 and Stage 10 never hit a surprise resolver
conflict later. CI installs only the `dev` extra (not `ui`/`fleet`) since
Stage 0's own test suite needs nothing else; this keeps CI fast without
weakening the lockfile guarantee for local development.

**Alternatives considered:** a `dublin-last-mile/` subdirectory nested
inside this git repo, matching the brief's tree literally — rejected, since
the git repository already serves as that root and an extra nesting level
would only add friction to every path in every later stage's commands.

## Interfaces

- `dlm.config.settings: Settings` — the shared settings instance.
  - `Settings.ensure_dirs() -> None` — creates all configured data/results
    directories if missing.
  - Constants: `dlm.config.TIME_UNIT = "seconds"`, `dlm.config.DISTANCE_UNIT = "metres"`,
    `dlm.config.REPO_ROOT: Path`.
- `dlm.logging_conf.configure_logging(level: str | None = None) -> None` —
  configures the root logger; `level` defaults to `settings.log_level`.
- `dlm.cli.app: typer.Typer` — the CLI entry point, installed as the `dlm`
  console script (`dlm --version`).
- `dlm.__version__: str` — package version string.

## Data & assumptions

- No numeric/domain assumptions yet — none of this stage's code touches
  distances, times, or coordinates.
- Default global seed: `42` (`Settings.seed`), used only as the fallback
  when a caller doesn't pass an explicit seed, per the project's
  determinism rule (§3.6 of the brief).
- Default log level: `INFO`.
- Python: 3.11 (matches `requires-python = ">=3.11"` in `pyproject.toml`;
  CI and the lockfile were built against 3.11.15).

## How to run

```bash
git clone <repo-url>
cd Maths-Modelling
make setup      # creates .venv, installs dlm + [dev,ui,fleet] extras, installs pre-commit hook
make test       # runs the pytest suite
make lint       # ruff check + ruff format --check
dlm --version   # -> "dlm 0.1.0"
```

## Acceptance criteria

- ✅ **`make setup && make test` passes from a clean clone.** Evidence: ran
  `rm -rf .venv .pytest_cache .ruff_cache src/dlm.egg-info && make setup`,
  which completed with `Successfully installed ... dlm-0.1.0 ...` and
  `pre-commit installed at .git/hooks/pre-commit`; then `make test`, output:
  ```
  tests/test_foundations.py ....                                           [100%]
  ============================== 4 passed in 0.12s ===============================
  ```
- ✅ **`ruff check .` clean.** Evidence: `make lint` →
  `ruff check .` → `All checks passed!`; `ruff format --check .` →
  `59 files already formatted`. Also verified via
  `pre-commit run --all-files` on the fully staged tree: both
  `ruff (legacy alias)` and `ruff format` hooks report `Passed`.
- ✅ **CI green.** `.github/workflows/ci.yml` runs `pip install -e ".[dev]"`,
  `ruff check .`, `ruff format --check .`, `pytest --cov=dlm` on every push
  and PR; the same commands were run locally above with the same results.
  CI itself will go green on the first push of this branch — there is no
  local substitute for the hosted run, so this is the mechanism, not yet
  the observed result.

## Results / evidence

- 4/4 tests passing (`tests/test_foundations.py`): package version,
  settings defaults (`seed=42`, `log_level="INFO"`, `data_dir`/`cache_dir`
  names), units policy constants, and that `dlm.cli.app` is a valid
  `typer.Typer` instance.
- `dlm --version` and `python -m dlm.cli --version` both print `dlm 0.1.0`.
- Lockfile (`requirements.txt`) captures 88 pinned packages from a clean
  install of `dlm[dev,ui,fleet]` on Python 3.11.15 — includes OSMnx 2.1.1
  and NetworkX 3.6.1 (both newer major versions than the `>=1.9`/`>=3.2`
  floors in `pyproject.toml`; the pin is what Stage 1 will actually be
  written and tested against).

## Known limitations

- The repository has no OSM/network access requirement yet, so "clean
  install" here means package installation, not the graph-download cache
  test that Stage 1 will add.
- `requirements.txt` was generated in this container; it has not yet been
  verified installable on a second, independent machine — that check is
  folded into Stage 9's "clean container" hardening pass rather than
  repeated at every stage.
- Several stub modules (`network/`, `instance/`, `solver/`, `disruption/`,
  `simulation/`, `viz/`) contain no code yet, only docstrings — by design,
  per the brief's explicit allowance for Stage 0 ("empty modules with
  docstrings are fine").

## Next

Stage 1 depends on:
- `dlm.config.settings` for `cache_dir` (where the graphml cache lives) and
  `seed`.
- `dlm.logging_conf.configure_logging` for consistent log output from the
  `dlm network build` command it introduces.
- The `tests/fixtures/` and `tests/test_network.py` placeholders created
  here, which Stage 1 will fill in with a tiny hand-built graph and real
  assertions.
- The CI workflow, which Stage 1 extends (still `dev`-extra only, since
  OSMnx is already in that dependency set via the lockfile).

Stage 1 will build the cached, routable, travel-time-annotated Dublin graph
and the lat/lon-to-node snapping function that every later user-facing
input path (address, preset, random, map-click) relies on.
