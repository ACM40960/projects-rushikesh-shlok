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

## Stage 1 — Dublin road network (2026-08-16)

- `dlm.network.loader.build_graph`: downloads the Dublin drive network from
  the public Overpass API, cached to `data/cache/dublin_<type>_<hash>.graphml`
  keyed by (bbox, network_type, simplify, OSMnx version); reduces to the
  largest strongly connected component (dropped 71 nodes / 96 edges from
  10,970/24,933 downloaded to 10,899/24,837 final).
- `dlm.network.travel_time`: per-`highway`-type default speed table
  (`speed_defaults.yaml`, Irish default speed limits) plus OSM `maxspeed`
  parsing (handles km/h, mph, and OSM's list-valued tags); assigns
  `speed_kph`/`speed_source`/`travel_time` (seconds) to every edge. 98.2%
  of edges in the built graph have a real OSM `maxspeed` tag.
- `dlm.network.snapping.snap_to_node`: lat/lon → nearest routable node with
  a configurable max-distance guard, raising a human-readable `SnapError`
  (not a silent bad snap or a stack trace) when nothing routable is close.
- `dlm network build` / `dlm network stats` CLI commands.
- ADR-0002: fetches OSM data via `curl` subprocess rather than OSMnx's own
  `requests`-based transport, which was found to hang indefinitely or reset
  connections unpredictably in this environment; `curl` fails fast and
  predictably, making retry-with-backoff possible. Full diagnosis in
  `docs/adr/ADR-0002-overpass-http-transport.md` and
  `docs/stages/stage-01-network.md`.
- `docs/data.md`: OSM/ODbL provenance, the speed table and its basis, and
  the Overpass reliability workaround.
- 18 tests (12 offline against a hand-built fixture graph, 6 against the
  real cached Dublin graph, marked `@pytest.mark.network`): travel-time
  imputation, one-way/SCC structure, snapping success/failure, strong
  connectivity, a hand-checked UCD Belfield → Trinity College route, a real
  one-way street, and the Irish Sea snapping failure.
