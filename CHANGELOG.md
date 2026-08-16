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

## Stage 6 — Experiment core: T1/T2/T3 (2026-08-16)

- `dlm.simulation.execution`: `InformationModel` (`omniscient`/`reactive`)
  and `execute_solution` drive a `Solution` over a disrupted graph edge by
  edge — `omniscient` re-plans each leg fresh, `reactive` walks the
  original path and only detours from the exact point a closure is
  discovered. Either can be infeasible per leg (no path from the
  discovery point onward) — a first-class, reported outcome.
- `dlm.simulation.replan.replan_from_blockage`: re-optimises the
  not-yet-served stops from wherever a `reactive` execution first hit a
  closure (`TwoOptSolver`, unchanged), anchoring `T3` to a realistic
  dispatcher-reacts-to-a-blocked-driver trigger, per the Stage 0
  scaffolding's own description of `replan.py`'s job.
- `dlm.simulation.metrics`: `compute_t2`/`compute_t3`/`compute_saving`,
  plus `compute_t3_oracle` (a from-scratch full-route re-solve, the
  `T3_oracle` the glossary anticipated) — all four share one
  `_total_service_time_s` helper, since service time never depends on
  routing.
- `dlm compare --instance X --scenario Y` CLI: prints `T1`/`T2`
  (omniscient + reactive)/`T3`/`T3_oracle`/`Saving %` and writes
  `results/<instance>-vs-<scenario>-<timestamp>/`.
- Real findings: `liffey_quays_closure` strands a `reactive` driver
  (infeasible) that `omniscient` and a full `T3_oracle` replan both
  recover from; `demo_single_edge_closure` (a new, narrow single-edge
  scenario built to force a clean detour) measures `T3_oracle > T3` —
  proof that "more information, re-solved from scratch" isn't a strict
  guarantee once both sides share the same heuristic solver, reported
  honestly rather than smoothed over.
- Amendment to the glossary's anticipated design: `InformationModel` has
  two values, not three — infeasibility is a `feasible: bool` outcome on
  `T2Result`/`T3Result`, not a third kind of driver knowledge.
- 17 new tests (113 total): 10 offline (a hand-built "diamond" graph with
  a genuine alternate route, every number derived and cross-checked —
  omniscient/reactive divergence, blockage detection, the promised
  `T1==T2==T3` no-op regression test, `T3_oracle` mechanics) + 7
  network-marked (all four curated scenarios end-to-end, the real
  infeasibility/recovery finding above).

## Stage 7 — Results harness (2026-08-16)

- `dlm.disruption.generators.generate_random_scenario`/
  `generate_random_scenarios`: seeded random disruptions drawn from a
  graph's own real nodes/edges (a real edge, a real node, a short
  real-edge random walk for a corridor, a small box around a real node
  for a polygon) — deterministic given `(seed, graph)`, always resolves.
- `dlm batch`: runs `T1`/`T2` (omniscient + reactive)/`T3`/`T3_oracle`/
  `Saving %` across every (instance, scenario) pair (default: 3 canonical
  instances x 14 scenarios = 42 runs, ~9 minutes), writes
  `results/batch-<timestamp>/` and a committed
  `docs/report/batch_results.csv`.
- `dlm.viz.figures` + `dlm figures`: three colourblind-safe (Okabe-Ito),
  PNG+SVG report figures built purely from that CSV — no solver/
  disruption/simulation dependency, so figures regenerate instantly.
- `dlm sensitivity`: sweeps `default_service_time_s` — the concrete
  check ADR-0004 (Stage 4) asked for. Real answer: service time is
  **35-44% of `T1`** at the current 180s default, **15-57%** across a
  60-300s sweep — a genuinely large sensitivity, documented as an update
  to ADR-0004 rather than left as a hypothetical.
- `make experiment` / `make figures` wired to `dlm batch` / `dlm
  figures` (previously stub targets since Stage 0).
- Real finding: of 30 seeded-random scenarios in the default batch, 0
  affected any canonical instance's route (despite every one of them
  closing/slowing real edges) — routes touch a tiny fraction of the
  graph's 62,068 edges, which is exactly why the curated, real-world-
  anchored scenario library is what produces every non-trivial `T2`/`T3`
  result. Reported directly, including in the `Saving %` distribution
  figure (a real single spike at 0%), not hidden.
- 22 new tests (135 total), all offline: 16 for
  `dlm.disruption.generators` (determinism, every shape x effect
  combination, real-graph resolution) + 6 for `dlm.viz.figures`
  (including infeasible-row and empty-distribution edge cases).

## Stage 8 — Fleet & benchmark (2026-08-16)

- `dlm.solver.clarke_wright.ClarkeWrightSolver`: parallel-savings CVRP
  construction (`fleet_size` vehicles, `vehicle_capacity` respected) +
  per-route 2-opt (Stage 4's `two_opt_improve`, reused unchanged). The
  savings formula needs no asymmetric-graph adaptation, unlike 2-opt's
  own classical delta trick.
- `dlm.solver.ortools_solver.OrToolsSolver`: the fixed OR-Tools benchmark
  oracle (ADR-0001) — one `RoutingModel` handles `fleet_size == 1` and
  `> 1` alike, with an optional capacity dimension and, when stops carry
  `time_window`s, VRPTW support the hand-implemented solvers don't
  attempt (a documented scope boundary, not a gap to close).
- `dlm.solver.base.FleetSolution`: multi-vehicle routes, reusing
  `Solution`/`Leg` per vehicle; unfit stops land in `.unassigned`, never
  silently dropped.
- `dlm instance new --vehicle-capacity`, `dlm instance add --demand`;
  `dlm plan` auto-detects `fleet_size > 1`; new `dlm benchmark` command.
- New canonical instance `fleet` (K=3, capacity=10, 15 stops, demand
  exactly matching total capacity).
- Real finding: `dlm benchmark` measures OR-Tools beating the hand-
  implemented solvers by 1.5% (`small`, K=1) and 1.8% (`fleet`, K=3) —
  both hand-implemented solves complete in under a millisecond, OR-Tools
  given an 8s budget finds a modestly better answer, the expected shape
  of result for a fast heuristic vs. a real metaheuristic.
- 11 new tests (146 total): 7 offline (a hand-built chain graph whose
  savings values are hand-derivable, capacity/fleet-size interaction) +
  4 network-marked (the canonical `fleet` instance, OR-Tools K=1/K>1
  parity, the VRPTW drop-one-stop demonstration).

## Stage 9 — Hardening (2026-08-16)

- `make reproduce`: chains `dlm network build` -> `dlm batch` ->
  `dlm figures` -> `dlm sensitivity` -> `dlm benchmark`, regenerating
  every number and figure the report depends on from a cold cache
  (~10 minutes warm-cache, measured).
- Fixed `make network`: it had been left as a Stage-0 stub ("lands in
  Stage 1") for eight stages despite `dlm network build` existing since
  Stage 1 — nothing exercised the Makefile target itself, only the CLI
  command directly, so it went unnoticed until this stage's grep-based
  audit.
- `docs/limitations.md`: consolidated from the "Known limitations"
  section of all nine prior stage docs, organised by theme.
- `docs/cli.md`: full reference for all 20 `dlm` subcommands.
- `docs/architecture.md`: rewritten from "first draft / planned
  pipeline" to describe the real, completed pipeline, with a per-stage
  "what it actually added" section.
- Hardening checks: a from-scratch `venv` (independent of this
  project's working `.venv/`) installs `.[dev,ui,fleet]` cleanly and
  passes the full 146-test suite; `dlm batch`/`dlm sensitivity` produce
  byte-identical CSVs across two independent runs.

## Stage 10 — UI (2026-08-16)

- `app/state.py`: `st.session_state` helpers plus thin orchestration
  (`run_plan`, `run_compare`, instance CRUD, listings) — the same
  `dlm.*` call sequence `dlm.cli`'s `plan`/`compare` commands use, no
  new domain logic.
- `app/main.py`: single scrolling Streamlit page — instance
  builder (preset/address/lat-lon/random/map-click), instance map,
  `T1` plan section (single-vehicle or fleet), disruption comparison
  section (`T2`/`T3`/`Saving %` + before/after maps). Map-click-to-add-
  a-stop wires through to `StopSource.MAP_CLICK`, exactly as
  `builder.py` documented since Stage 2 that Stage 10 would.
- `make app`: wired to `streamlit run app/main.py` (was a Stage 0 stub).
- `tests/test_cli_ui_parity.py`: 4 new tests using `typer.testing
  .CliRunner` to run `dlm plan`/`dlm compare` in-process and assert
  `app.state`'s equivalent functions produce identical `T1`/`T2`/`T3`
  for the same inputs — single-vehicle, fleet, a disruption comparison,
  and the fleet-instance rejection. 150 tests total, all pass.
- Verified in a real browser (headless Chromium via Playwright): golden
  path (load instance -> view map -> plan -> compare) and an infeasible-
  `T2`/`T3` edge case both render correctly; screenshots committed to
  `docs/report/ui_*.png`.
