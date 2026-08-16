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

## Stage 2 — Dynamic delivery instances (2026-08-16)

- `dlm.instance.schema`: `Stop`/`Depot`/`Instance` models; `Instance.n_stops`
  is a property, never a stored constant, per the project's dynamic-`N`
  requirement.
- `dlm.instance.builder.InstanceBuilder`: mutable builder with
  `set_depot_from_*`/`add_stop_from_*` (address / lat-lon / preset /
  seeded-random), `move_stop`/`remove_stop`/`rename_stop`, save/load, and
  `build()` — the single point where business-rule validation (depot set,
  `1 <= N <= 50`, no depot/stop node collision) runs, raising every problem
  found at once.
- `dlm.instance.geocode`: cached Nominatim geocoding (curl-based, per
  ADR-0002's pattern) with ambiguity detection — returns candidates
  instead of guessing when a query matches multiple genuinely distinct
  real places.
- ~30 curated, real, geocoded Dublin location presets
  (`data/presets/dublin_locations.yaml`) spanning hospitals, universities,
  retail, suburbs, transport hubs, and landmarks.
- `dlm instance new/add/remove/move/rename/random/list/show/map` CLI, all
  thin wrappers over `InstanceBuilder`.
- `dlm.viz.folium_map`: `dlm instance map` renders a standalone Folium HTML
  map of an instance's depot and stops.
- Three canonical instances committed: `small` (N=8), `medium` (N=20),
  `large` (N=40), built entirely from named Dublin locations (presets +
  real addresses), not random points.
- ADR-0003: expanded `DEFAULT_BBOX` to Greater Dublin (several curated
  presets — the airport, outer suburbs — fell outside Stage 1's original,
  smaller area); this in turn forced the graph cache format to change from
  `.graphml` to pickle, since the larger graph missed Stage 1's <5s
  cache-load bar under graphml (10.42s vs. pickle's 0.44-1.3s). Both
  recorded as amendments in `docs/stages/stage-02-instances.md`, not made
  silently.
- 21 new tests (8 offline, 13 real-network-marked): the full N=1/2/3/8/20/40
  sweep, add→remove→add content-equality, lossless save/load round-trip,
  address/preset/lat-lon resolving to the same node, Irish Sea failure,
  ambiguous-address candidates, and canonical-instance validation.
- `experiments/render_map_screenshot.py`: a curl-relay workaround (same
  pattern as ADR-0002, one layer up) for this sandbox's headless-browser
  networking, used once to produce the committed
  `docs/report/instance_map_small.png` acceptance-evidence screenshot.

## Stage 3 — Travel-time matrix (2026-08-16)

- `dlm.instance.matrix.Matrix`: cached, `O(1)`-lookup cost + full-path
  matrix over an instance's depot + stops, built with one Dijkstra per
  point (not one per pair).
- Incremental `add_point`/`remove_point`/`move_point`: adding one point
  costs exactly two more Dijkstras (directed graph, one search each way
  via a reversed graph view), never a full rebuild — measured **14.5x**
  faster than a full rebuild on a real 21-point instance, with byte-identical
  results.
- `recompute_on(graph)`: rebuild the same point set against a different
  graph (for Stage 5's disrupted views), bypassing the disk cache.
- Disk cache keyed by `(graph identity, sorted node set, weight)`, shared
  across instances with the same points; cache hit is near-instant
  (0.004s vs. 13.9-15.5s for a fresh 21-point build).
- `MatrixStats`: asymmetry rate (>95% on every real Dublin instance tested
  — the expected signature of a genuinely directed, one-way-heavy street
  network) and triangle-inequality violation count (zero on every matrix
  built, which is a correctness guarantee for shortest-path costs on a
  single static graph, not a coincidence — explained in
  `docs/stages/stage-03-matrix.md`).
- `dlm instance matrix` CLI command.
- 12 new tests (51 total): hand-derived costs/paths on the tiny fixture
  graph (independently computed, not just re-calling networkx), incremental
  vs. full-rebuild equality and speedup (both fixture and real graph),
  save/load round-trip, and the N=20 build-time/cache-instant acceptance
  criteria.

## Stage 4 — Baseline solver and T1 (2026-08-16)

- `dlm.solver.base`: `Solution`/`Leg`/`Solver` protocol, shared by every
  solver including the future OR-Tools benchmark (Stage 8).
- `dlm.solver.nearest_neighbour.NearestNeighbourSolver`: greedy
  construction using the matrix's directed cost (asymmetry-aware).
- `dlm.solver.two_opt.TwoOptSolver`: 2-opt improvement with full directed
  route-cost re-evaluation per candidate move — not the O(1) symmetric
  delta trick, which is unsound given Stage 3's >95% asymmetry rate.
  First-improvement, capped at 2000 iterations, logs its trajectory.
- `dlm.simulation.metrics.compute_t1`: `T1` = driving time + per-stop
  service time (falling back to `settings.default_service_time_s = 180.0`,
  a placeholder flagged in ADR-0004 pending author confirmation), with a
  full per-leg breakdown.
- `dlm.viz.folium_map.render_route_map`: draws the solved route's actual
  street-following geometry (real node paths, not straight lines).
- `dlm plan --instance X` CLI: writes a self-describing
  `results/<instance>-<timestamp>/{config.yaml, result.json, route_map.html}`.
- Amendment to Stage 3: `Matrix` gained a `distance` field (metres along
  the shortest-*time* path), since the brief's `Solver.solve(instance,
  matrix)` protocol has no graph parameter for solvers to compute distance
  independently; matrix cache key bumped to invalidate old caches missing
  the field.
- Real numbers: `small` (N=8) T1 = 3278.1s (1838.1s drive + 1440.0s
  service, 21,961m); `medium` (N=20) T1 = 10,366.8s, 2-opt improved the
  route 6.8% over nearest-neighbour alone; `large` (N=40) T1 = 19,019.3s,
  2-opt improved it 1.6%. All three solve in under 100ms.
- 15 new tests (66 total): a known optimum on the Stage 3 fixture graph
  (verified by exhaustively checking all 6 permutations, not just
  asserted), monotone 2-opt trajectories, degenerate sizes (N=0/1/2),
  N=8/20/40 on the real canonical instances, T1 breakdown consistency, and
  re-run determinism.

## Stage 5 — Disruption engine (2026-08-16)

- `dlm.disruption.schema`: `Disruption` (shape: edge/node/corridor/polygon
  x effect: closure/slow_zone) and `Scenario`; `load_scenario` /
  `list_scenarios` / `find_scenario`, mirroring `instance/presets.py`'s
  pattern.
- `dlm.disruption.engine.apply_scenario`: resolves every disruption's
  geometry **once, up front, against the pristine graph** (so a corridor's
  shortest-path resolution never depends on scenario list order), then
  applies effects to a **copy** of the graph — closures always beat slow
  zones on the same edge, first-listed wins within one effect (both
  documented, not silent). `DisruptionResult.revert()` undoes changes on
  that copy in place — measured **0.0003s** on the real graph (53 closed
  edges), versus ~1.3s for a fresh `apply_scenario` copy.
- `validate_scenario`: resolves a scenario's geometry without applying
  anything, for `dlm disrupt validate`.
- Four curated, cited, real Dublin scenarios (`scenarios/library/`): the
  St Patrick's Day parade route (corridor closure, 53 edges), an
  O'Connell Street protest (polygon closure, 58 edges), Luas Cross City
  works on Dawson Street (corridor slow zone, 2 edges), and a north-quays
  incident closure (corridor closure, 44 edges).
- `dlm.viz.folium_map.render_disruption_map`: closed edges in red, slowed
  in orange, along each edge's real OSM geometry.
- `dlm disrupt list` / `validate` / `preview` / `new` CLI.
- Real finding: all three closure scenarios genuinely disconnect the real
  Dublin graph (strongly-connected before, not after); the slow-zone
  scenario never can, since it never removes an edge — reported as-is,
  exactly the situation Stage 6's `infeasible` information-model state
  exists for.
- Amendment to Stage 0: the `src/dlm/disruption/library/` stub directory
  (a README only) is retired — curated scenario YAML now lives in
  `scenarios/library/`, matching `scenarios/README.md`'s own description
  of where curated scenarios belong.
- 30 new tests (96 total): 19 offline (schema validation, all shape x
  effect combinations on the fixture graph, revert exactness, the
  up-front-resolution and closure-beats-slow-zone conflict rules) + 11
  network-marked (all four curated scenarios validate and apply cleanly,
  the disconnection finding, revert timing on the real graph).
