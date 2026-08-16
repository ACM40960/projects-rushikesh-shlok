# Stage 04 — Baseline solver and T1

## Goal

A planned route on the real Dublin network, and the first real number.
Before this stage, the project could build instances and look up
travel times between points; after it, it can answer "what's the best
order to visit these stops, and how long does that take" — the actual
question the whole project exists to ask.

## Scope

**In scope:**
- `Solution`/`Leg`/`Solver` (`dlm.solver.base`), the shared shape every
  solver — including Stage 8's OR-Tools benchmark — will return.
- Nearest-neighbour construction (`dlm.solver.nearest_neighbour`).
- 2-opt improvement with correct directed-cost re-evaluation
  (`dlm.solver.two_opt`).
- `T1` (`dlm.simulation.metrics.compute_t1`): driving time + service time,
  with a per-leg breakdown.
- `dlm plan --instance X` CLI, writing a self-describing run directory.
- A route map showing the solved route's real street geometry.

**Explicitly out of scope** (land in the stage noted):
- `T2`/`T3`/`Saving %` — Stage 6, once a disruption exists to define them
  against.
- Multi-vehicle (`K>1`), capacity, OR-Tools — Stage 8.
- Any lateness penalty / time windows — Stage 8 (VRPTW).

## Design

**The `Matrix` grew a `distance` field.** The brief's `Solver` protocol is
`solve(instance, matrix) -> Solution` — no graph parameter. Stage 3's
`Matrix` stored cost and path but not distance, which would have forced
every solver to also hold a graph reference just to compute
`total_distance_m`. Extended `Matrix.add_point` to also record
`distance[(u, v)]` (metres along that same shortest-*time* path — not an
independent shortest-*distance* search), computed by summing edge
`length` along the Dijkstra-returned path, picking the same parallel edge
Dijkstra would have (minimum `travel_time` among parallel edges) so the
recorded distance is consistent with the recorded cost. The matrix cache
key gained a schema-version component (`_MATRIX_SCHEMA_VERSION = 2`) so
this change transparently invalidates old caches rather than silently
returning matrices missing the new field. This is an amendment to Stage
3, in the same spirit as Stage 2's amendments to Stage 1: a concrete need
from a later stage, applied to the earlier module rather than worked
around.

**Solver design and the asymmetric-2-opt problem** are written up in full
in `docs/modelling.md` (objective function, why NN+2-opt, why full
directed-cost re-evaluation instead of the O(1) symmetric delta trick,
complexity). The short version: reversing a route segment on an
*asymmetric* graph changes the cost of every internal edge in that segment
(each is now traversed in the opposite direction), so the classic O(1)
2-opt delta — valid only when `d(x,y) == d(y,x)` — is wrong here. Stage 3
measured >95% of point pairs asymmetric, so this isn't a theoretical
nicety; a naive delta-based 2-opt would silently "improve" the route using
wrong cost estimates. `two_opt_improve` recomputes each candidate's full
route cost instead (`route_time_s`, `O(N)` per candidate), which at
`N <= 50` costs a fraction of a second (see Results).

**`T1` lives outside the solver, on purpose.** `Solution.total_time_s` is
pure driving time; `compute_t1` adds service time. A solver that doesn't
know about service time is simpler and stays reusable (Stage 8's OR-Tools
solver returns the same `Solution` shape), and nothing is lost: a constant
per-stop cost added regardless of visit order can never change which
order minimises driving time, so the solver's job is unaffected by not
knowing about it.

**Service time default is a flagged placeholder, not a researched
figure.** `default_service_time_s = 180.0` (config.py) is substituted
whenever a stop's `service_time_s` is `0.0` (unset — no code sets it to
anything else yet). Raised as **ADR-0004** (Proposed, not Accepted) per
§9 of the brief, since this is a modelling choice for the authors, not
something an engineer should decide unilaterally the way ADR-0002/0003
were.

**Run directories are self-describing.** `dlm plan` writes
`results/<instance>-<timestamp>/{config.yaml, result.json, route_map.html}`
— `config.yaml` names exactly what produced the result (instance, solver,
seed, service-time default, graph cache key), per the project's
determinism rule (§3.6 of the brief: "every run writes its config to its
output directory").

**Alternatives considered:**
- **Or-opt/relocate instead of 2-opt** — the brief explicitly offered this
  as the asymmetry-safe alternative (never reverses a segment, so no
  directed-cost re-evaluation problem exists to begin with). Rejected:
  2-opt is the fixed technical decision (ADR-0001); the full-recomputation
  fix for its asymmetry problem is simple enough that the alternative
  wasn't needed.
- **Best-improvement 2-opt** (search all candidates per pass, apply only
  the single best) instead of first-improvement — rejected for the
  hand-implemented v1: first-improvement is simpler to trace by hand for
  a viva, at the cost of possibly more iterations; both are within the
  `O(N^3)`-per-pass budget at this problem's scale.

## Interfaces

- `dlm.solver.base`: `Leg`, `Solution` (`order`, `legs`, `total_time_s`,
  `total_distance_m`, `meta`), `Solver` protocol, `build_solution(instance,
  matrix, order, meta) -> Solution`, `route_time_s(instance, matrix, order) -> float`.
- `dlm.solver.nearest_neighbour.NearestNeighbourSolver`.
- `dlm.solver.two_opt.TwoOptSolver(max_iterations=2000)`,
  `two_opt_improve(instance, matrix, order, max_iterations) -> (order, trajectory)`.
- `dlm.simulation.metrics.compute_t1(instance, solution, default_service_time_s=None) -> T1Result`
  (`drive_time_s`, `service_time_s`, `total_time_s`, `distance_m`,
  `n_stops_served`, `legs: list[LegMetric]`).
- `dlm.viz.folium_map.render_route_map(instance, solution, graph) -> folium.Map`,
  `save_route_map(...)`.
- `dlm.instance.matrix.Matrix.get_distance(u, v) -> float` (new this stage).
- CLI: `dlm plan --instance X [--solver nn_2opt|nearest_neighbour]`.

## Data & assumptions

- `default_service_time_s = 180.0` (see ADR-0004 — flagged, not final).
- 2-opt iteration cap: 2000 (never approached at this project's instance
  sizes — see Results).
- Units: as always, time in seconds, distance in metres.

## How to run

```bash
source .venv/bin/activate
dlm plan --instance small
dlm plan --instance medium --solver nearest_neighbour   # skip 2-opt, for comparison
```

## Acceptance criteria

- ✅ **On a tiny fixture with a known optimum, NN+2-opt finds it.**
  `tests/test_solvers.py::test_two_opt_finds_the_known_optimum`: on the
  Stage 3 fixture graph (depot=node 1, stops at nodes 2/3/4), NN's greedy
  choice happens to produce the *worst* of the 6 possible tours (73.14s —
  a genuinely illustrative, not cherry-picked, property of this fixture),
  and 2-opt improves it in one reversal to 45.24s — verified to be the
  true minimum by exhaustively checking all 6 permutations in the test
  itself, not just asserted.
- ✅ **2-opt never increases cost; log shows monotone improvement.**
  `test_two_opt_trajectory_is_monotone_non_increasing` (fixture) and
  `test_two_opt_never_increases_cost_on_real_instances` (all three
  canonical instances) assert the trajectory is non-increasing throughout.
  `TwoOptSolver` logs each pass's iteration count and cost delta via
  `logger.info` (see Results for a real example).
- ✅ **Solver runs correctly for N = 1, 2, 8, 20, 40 (parametrised test).**
  `test_solvers_handle_degenerate_sizes_without_crashing` (N=0,1,2 on the
  fixture — 0 included as a bonus edge case beyond what the brief asks)
  and `test_solver_runs_correctly_for_n_8_20_40` (the three canonical
  instances, real graph).
- ✅ **`T1` reported with a breakdown: driving time, service time,
  distance, per-leg table.** `dlm plan` prints all four; `result.json`
  carries the full per-leg table. See Results below for real numbers.
- ✅ **Baseline route HTML map opens and visually follows real streets
  (not straight lines).** `docs/report/route_map_small.png` — a real
  rendered screenshot (same curl-relay workaround as Stage 2's
  `experiments/render_map_screenshot.py`) showing the `small` instance's
  route bending through actual Dublin streets between Dublin Port and its
  8 stops, not straight lines between markers.
- ✅ **Re-running the same command gives identical `T1`.**
  `test_rerunning_the_same_instance_gives_identical_t1`, and reproduced
  directly via the CLI (see Results) — both NN construction and 2-opt are
  fully deterministic (no randomness anywhere in either algorithm).

All 66 tests pass (`pytest -v`: 51 from Stages 0-3 + 15 new this stage, 7
offline / 8 network-marked within it); `ruff check .` / `ruff format
--check .` clean.

## Results / evidence

```
$ dlm plan --instance small
solver:            nn_2opt
order:             s5 -> s1 -> s6 -> s4 -> s7 -> s2 -> s3 -> s8
drive time:        1838.1s
service time:      1440.0s (8 stops)
T1 (total time):   3278.1s
distance:          21961.4m

$ dlm plan --instance medium
drive time:        6766.8s
service time:      3600.0s (20 stops)
T1 (total time):   10366.8s
distance:          101885.9m

$ dlm plan --instance large
drive time:        11819.3s
service time:      7200.0s (40 stops)
T1 (total time):   19019.3s
distance:          159515.6m
```

2-opt improvement, NN-only vs. NN+2-opt (from `Solution.meta`):

| Instance | NN alone | NN + 2-opt | Improvement | Accepted moves | Solve time |
|---|---|---|---|---|---|
| small (N=8) | 1838.1s | 1838.1s | 0% (NN already optimal here) | 0 | <20ms |
| medium (N=20) | 7261.6s | 6766.8s | 6.8% | 8 | 13ms |
| large (N=40) | 12016.4s | 11819.3s | 1.6% | 5 | 84ms |

`small`'s zero improvement is reported honestly, not hidden: for this
particular 8-stop instance, nearest-neighbour's greedy construction
happened to already be 2-opt-optimal (no single reversal improves it).
This is a real, unremarkable outcome for small instances — 2-opt has
fewer candidate moves to find an improvement among — and is exactly why
`medium` and `large` are also reported: both show 2-opt earning its place
in the pipeline.

Re-running `dlm plan --instance small` a second time reproduced the
identical `T1 = 3278.1s` (see the acceptance criteria section).

## Known limitations

- `default_service_time_s` is a placeholder pending author confirmation —
  ADR-0004.
- 2-opt is first-improvement, not best-improvement; may take marginally
  more iterations to converge than a best-improvement variant, though not
  measurably at this project's scale (all three canonical instances solve
  in under 100ms).
- No lateness penalty or time windows yet (Stage 8 / VRPTW).
- `default_service_time_s` is a single global constant, not
  category-dependent (a hospital stop and a residential drop-off are
  assumed equally quick) — see ADR-0004's open question.

## Next

Stage 5 depends on:
- `Solution`/`Leg` as the shape the disruption engine's "was this route
  affected" check will inspect (does any leg's path use a closed edge).
- `dlm plan`'s run-directory convention, which Stage 6's `dlm compare`
  will follow for its own T1/T2/T3 output.
- `route_time_s` (`dlm.solver.base`) as the building block Stage 6's
  re-optimisation (`T3`) will reuse to re-solve a sub-instance from the
  vehicle's current position.

Stage 5 will build the disruption engine: YAML scenarios, closures, and
slow zones applied to a *view* of the graph without mutating the base
graph.
