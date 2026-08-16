# Stage 05 — Disruption engine

## Goal

A disruption that actually changes the graph. Before this stage, the
project could plan a route on the *normal* Dublin network; after it, a
named, citable, YAML-defined event (a parade, a protest, roadworks, a
quay closure) can be turned into a concrete change to that network — some
streets impassable, others slower — without ever touching the base graph.
This is the piece `T2`/`T3` (Stage 6) will be computed against.

## Scope

**In scope:**
- `Disruption`/`Scenario` schema (`dlm.disruption.schema`): four shapes
  (edge/node/corridor/polygon) x two effects (closure/slow_zone).
- `apply_scenario`/`DisruptionResult`/`revert()`
  (`dlm.disruption.engine`): turns a `Scenario` into a graph *copy* with
  the effects applied, plus a full audit.
- `validate_scenario`: resolves a scenario's geometry against a real
  graph without applying anything — for authoring/CI use.
- A curated library of four real, cited Dublin scenarios
  (`scenarios/library/`).
- `dlm disrupt list` / `validate` / `preview` / `new` CLI.
- `render_disruption_map` (`dlm.viz.folium_map`): closed edges in red,
  slowed edges in orange, along real street geometry.

**Explicitly out of scope** (land in the stage noted):
- Actually executing a planned route on a disrupted graph, `T2`/`T3`,
  the information-model enum (`omniscient`/`reactive`/`infeasible`),
  re-optimisation — Stage 6.
- Seeded random/stochastic scenario generation for batch experiments —
  Stage 7 (`dlm.disruption.generators`, still a stub).
- Drawing a scenario interactively (polygon/corridor on a map) — Stage 10
  (the UI is a thin client over this stage's YAML schema and
  `apply_scenario`; it does not get its own application logic).

## Design

**Shape x effect, not five separate disruption types.** The glossary
(written in Stage 0) described disruptions as "edge/node/corridor/polygon
closure, or a slow zone" — five things. Building five separate models
would duplicate every geometry field across at least three of them. Instead
a `Disruption` is one model with two independent enums: **shape** (what
part of the graph — edge/node/corridor/polygon) and **effect** (what
happens to it — closure/slow_zone). Any shape can carry either effect (a
`polygon` + `slow_zone` models a neighbourhood under general roadworks; a
`polygon` + `closure` models a cordoned-off area) — one matcher function
per shape, one effect-applier per effect, instead of eight near-duplicate
code paths.

**Resolution happens once, up front, against the pristine graph — not
sequentially as each disruption is applied.** Every disruption's geometry
is resolved to a concrete edge set *before* any edge is removed or slowed.
This matters concretely for `corridor` shapes, whose resolution is itself
a shortest-path search: if an earlier disruption in the same scenario had
already removed part of the route a later corridor needs, sequential
resolution would make that corridor's outcome depend on list order (or
fail outright). `tests/test_disruption.py::test_resolution_happens_up_front_against_the_pristine_graph`
proves this directly: a closure listed before a corridor that needs the
very edge it removes still resolves correctly.

**Closures always beat slow zones on the same edge; first-listed wins
within one effect.** If two disruptions in a scenario target the same
edge, silently compounding two `slow_zone` multipliers (or letting the
last-listed one silently override) would make results depend on YAML
ordering in a way an author is unlikely to notice. Closures are applied
first regardless of list position (a road that's closed can't also be "a
bit slower"); slow zones are applied second, and skip any edge a closure
already removed. Overlaps within the same effect keep the first-listed
disruption, logged at debug level, not silently. This is deliberately
simple over deliberately realistic — see Known limitations.

**`apply_scenario` copies the graph; `revert()` undoes changes on that
copy in place, cheaper than re-copying.** `graph.copy()` on the full
Dublin graph (28,112 nodes / 62,068 edges) costs ~0.48s — most of
`apply_scenario`'s measured runtime (see Results). `revert()` only
touches the specific edges this application changed
(sub-millisecond, measured), which is what makes it worth having as its
own method rather than "just call `apply_scenario` again": Stage 10's
scenario-authoring UI can toggle a disruption on/off against the same
graph object repeatedly without paying the copy cost each time.

**Polygon matching tests node membership once, not per edge.** An edge is
"in" a polygon if either endpoint is — computed by classifying every graph
node in a single vectorised `shapely.contains_xy` call, then filtering
edges by node-set lookup, rather than constructing a `shapely.Point` per
edge (most nodes have 2-4 incident edges, so that would repeat the same
classification work several times per node).

**Corridor resolution reuses the shortest-path machinery already proven
in Stage 3/4** (`nx.shortest_path(..., weight="travel_time")`) rather than
requiring a scenario author to enumerate every edge of a street by hand —
2-6 waypoints is enough to describe a real route (see the curated
library's `st_patricks_day_parade.yaml`, 6 waypoints resolving to 53
edges).

**Alternatives considered:**
- **Mutating the graph in place and restoring it afterwards**, instead of
  copying. Rejected: any concurrent user of the same graph object (the
  Stage 6 comparison of T1 vs T2, which needs *both* the normal and
  disrupted graph at once) would break. A copy is the only option that
  keeps "the base graph is never mutated" actually true under concurrent
  use, not just under careful sequencing.
- **A single flat `Disruption` type discriminated by a string `kind`**
  covering all five glossary terms directly (`edge_closure`,
  `node_closure`, ... `slow_zone`), instead of shape x effect. Rejected:
  the five-`kind` version can't express "polygon slow zone" or "node slow
  zone" at all without adding more kinds, while shape x effect gets all
  eight combinations from four shape matchers and two effect appliers.

## Interfaces

- `dlm.disruption.schema`: `DisruptionShape`, `DisruptionEffect`,
  `Disruption`, `Scenario`, `load_scenario(path) -> Scenario`,
  `list_scenarios() -> list[Path]`, `find_scenario(name) -> Path`,
  `ScenarioNotFoundError`.
- `dlm.disruption.engine`: `apply_scenario(graph, scenario, at_time=None)
  -> DisruptionResult`, `DisruptionResult` (`graph`, `changes`, `revert()`,
  `n_edges_closed`, `n_edges_slowed`, `affected_edges`), `EdgeChange`,
  `validate_scenario(graph, scenario) -> ScenarioValidation`,
  `DisruptionResolutionError`.
- `dlm.viz.folium_map.render_disruption_map(result) -> folium.Map`,
  `save_disruption_map(result, path) -> Path`.
- CLI: `dlm disrupt list`, `dlm disrupt validate --scenario X`,
  `dlm disrupt preview --scenario X [--at-time S] [--out PATH]`,
  `dlm disrupt new --name X --shape S --effect E`.

## Data & assumptions

- Coordinates in scenario YAML are `[lat, lon]`, matching the project's
  convention everywhere else (not GeoJSON's `[lon, lat]`).
- `speed_factor` (slow zones) must be strictly between 0 and 1 — a value
  of 1 would be a no-op disruption, and closures (not a `speed_factor` of
  0) are how "impassable" is expressed.
- `severity` (0-1) is stored but not read by the engine — it's a hook for
  Stage 7's scenario generators (sampling weight), not part of the cost
  model. Flagged here so it isn't mistaken for something that currently
  scales the effect.
- `time_window`, when set, is seconds since scenario start; `at_time=None`
  (the default for `dlm disrupt preview` and `validate_scenario`) applies
  every disruption regardless of window, since there is no simulated
  clock yet — that lands with Stage 6's route execution.

## How to run

```bash
source .venv/bin/activate
dlm disrupt list
dlm disrupt validate --scenario st_patricks_day_parade
dlm disrupt preview --scenario liffey_quays_closure
dlm disrupt new --name my_scenario --shape corridor --effect closure
```

## Acceptance criteria

- ✅ **Scenario YAML parses into a validated `Scenario`/`Disruption`
  model**, rejecting malformed input at load time, not at use time.
  `tests/test_disruption.py`: 7 schema tests (missing/conflicting
  geometry fields per shape, `slow_zone` without `speed_factor`,
  `closure` with one set, `time_window` with `start >= end`, duplicate
  disruption ids, a full YAML round-trip).
- ✅ **Applying a scenario returns a new graph, closed edges are
  unreachable, unaffected edges keep their original cost.** 5 offline
  tests on the fixture graph (edge closure both directions and
  directional-only, node closure, slow-zone cost scaling, base-graph
  non-mutation asserted directly).
- ✅ **The base graph is never mutated.** Asserted directly in every
  offline test that applies a scenario, and again on the real graph
  (`test_curated_scenario_applies_without_mutating_base_graph`, edge count
  identical before/after for all four curated scenarios).
- ✅ **At least 3 real, distinct, cited Dublin disruption scenarios
  exist and apply cleanly to the real graph.** Four are shipped (one
  more than required) — see Results for their measured effect.
- ✅ **A disrupted-graph preview map visually shows the affected
  street(s).** `docs/report/disruption_preview_parade.png` — a real
  screenshot of `dlm disrupt preview --scenario st_patricks_day_parade`,
  the closed corridor (red) visibly following Parnell Square ->
  O'Connell Street -> O'Connell Bridge -> D'Olier Street/College Green ->
  Dame Street -> Christ Church, using each edge's actual OSM geometry
  (not straight lines).
- ✅ **Revert restores the graph to its exact pre-disruption state.**
  `test_revert_restores_original_graph_exactly` compares every edge's
  full attribute dict before and after apply+revert (fixture graph);
  `test_revert_is_fast_and_exact_on_the_real_graph` does the same by edge
  count on the real graph and asserts it completes in well under the
  0.5s bound (measured: sub-millisecond — see Results).

All 96 tests pass (`pytest -v`: 66 from Stages 0-4 + 30 new this stage, 19
offline / 11 network-marked within it); `ruff check .` / `ruff format
--check .` clean.

## Results / evidence

Applying all four curated scenarios to the real Dublin graph (28,112
nodes / 62,068 edges; `graph.copy()` alone measured at 0.48s, most of the
`apply_scenario` time below):

| Scenario | shape | effect | Edges affected | Apply time | Still strongly connected after? |
|---|---|---|---|---|---|
| `st_patricks_day_parade` | corridor | closure | 53 closed | 1.34s | **No** |
| `oconnell_street_protest` | polygon | closure | 58 closed | 0.65s | **No** |
| `liffey_quays_closure` | corridor | closure | 44 closed | 0.71s | **No** |
| `luas_works_dawson_street` | corridor | slow_zone | 2 slowed | 0.70s | Yes |

The three closure scenarios each genuinely disconnect the graph (a
cordoned-off street or area removes every route through it, and Dublin's
city-centre street grid does not have enough redundancy around O'Connell
Street/the quays to route around a fully closed corridor); the slow zone
never can, since it never removes an edge — reported honestly as a real
structural finding, not smoothed over. This is exactly the situation
Stage 6's "infeasible" information-model state exists for.

`revert()` on the parade scenario (53 closed edges) measured **0.0003s**
— restoring the graph to its original 62,068-edge state without
re-copying it, versus the ~1.3s a fresh `apply_scenario` call would cost.

```
$ dlm disrupt preview --scenario st_patricks_day_parade
scenario:          st_patricks_day_parade (scenarios/library/st_patricks_day_parade.yaml)
edges closed:      53
edges slowed:      0
strongly connected after: False
map written to:    results/disruption_previews/st_patricks_day_parade.html
```

## Known limitations

- **Closure-beats-slow-zone / first-wins conflict resolution is simple,
  not physically modelled.** A real overlapping closure+slow-zone (e.g. a
  parade route that also passes through a general-roadworks slow zone)
  collapses to "closed," which is realistic here, but two overlapping
  slow zones collapsing to "only the first counts" is a simplification —
  a more careful model might take the more severe of the two. Not needed
  at this project's scenario count (4 curated + whatever a user authors),
  and documented rather than silently accepted.
- **`severity` is not yet used by anything in this stage** — stored for
  Stage 7's generators, which don't exist yet. A `severity` value in a
  scenario file today has no effect on `apply_scenario`.
- **No time-of-day-aware `at_time` demo yet** — the `time_window` field
  and `apply_scenario`'s `at_time` parameter are implemented and tested,
  but every curated-scenario evidence run above used the default
  (`at_time=None`, ignore windows), since there is no simulated clock to
  drive it with until Stage 6.
- **Polygon/corridor resolution both use the *undisrupted* graph's
  geometry and shortest paths** — if a real-world scenario genuinely
  depends on one disruption physically altering the geometry another
  disruption's corridor should follow (not just which edges exist), that
  is out of scope; disruptions affect travel cost and reachability, never
  each other's own geometry resolution.

## Next

Stage 6 depends on:
- `DisruptionResult.graph` as the disrupted view a planned `Solution`
  (Stage 4) gets executed against, to compute `T2`.
- `DisruptionResult.affected_edges` / `changes` as exactly the "was this
  route affected" check `Solution.legs[*].path` needs (does any leg use
  an edge that's now closed or slowed) — this is what makes `T2 == T1`
  a valid, checkable outcome when a disruption misses a route entirely.
- The connectivity finding above (closures can and do disconnect the real
  graph) as the concrete trigger for the `infeasible` information-model
  state: a vehicle whose remaining route requires a now-closed edge has
  no frozen-route `T2` to report.
- `route_time_s` (`dlm.solver.base`, Stage 4) and `TwoOptSolver`
  (unchanged) as what Stage 6's re-optimisation (`T3`) re-runs against
  `DisruptionResult.graph` from the vehicle's current position.

Stage 6 will build the experiment core: executing a frozen route under a
disruption (`T2`), re-optimising from the current position (`T3`), the
information-model enum, and `dlm compare`.
