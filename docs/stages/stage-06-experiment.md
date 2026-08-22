# Stage 06 — Experiment core (T1/T2/T3)

## Goal

The three numbers the whole project exists to produce, and the fourth
(`Saving %`) that compares them. Before this stage, a disruption could
change a graph (Stage 5) but nothing yet asked "and what does that do to
the plan?" After it, `dlm compare` answers that question end to end: cost
under normal conditions, cost of blindly driving the same plan through
the disruption, cost of re-optimising once blocked, and what re-optimising
actually bought.

## Scope

**In scope:**
- `InformationModel` (`omniscient`/`reactive`) and `execute_solution`
  (`dlm.simulation.execution`): drives a `Solution` over a disrupted graph
  edge by edge, under either model.
- `replan_from_blockage` (`dlm.simulation.replan`): re-optimises the
  not-yet-served stops from wherever a `reactive` execution first hit a
  closure.
- `T2Result`/`T3Result`/`T3OracleResult` and `compute_t2`/`compute_t3`/
  `compute_t3_oracle`/`compute_saving` (`dlm.simulation.metrics`).
- `dlm compare --instance X --scenario Y` CLI.

**Explicitly out of scope** (land in the stage noted):
- Batch/seeded generation of many scenarios at once, sensitivity sweeps —
  Stage 7 (`dlm.disruption.generators`, still a stub).
- Multi-vehicle (`K>1`) re-optimisation, OR-Tools as the re-optimiser —
  Stage 8.
- A dedicated "replanned route" map visualisation (`dlm compare` reuses
  Stage 4/5's existing route/disruption maps rather than building a new
  renderer for `T3`'s synthetic mid-route starting point) — a Stage 10
  UI nicety if it turns out to matter, not required by any acceptance
  criterion here.

## Design

**`T2` needs an information model because "drive the same route" is not
one number.** A planned route is a fixed stop *order*; how much driving
it costs once a disruption exists depends on what the driver knows and
when. `omniscient` re-plans each leg fresh against the disrupted graph
(still the same stop order — only the path between two consecutive stops
can change). `reactive` walks the *original* leg's path node by node,
paying whatever a still-existing edge now costs (unaffected, or a slow
zone), and only deviates from that exact path the moment an edge is
actually gone — detouring from that point, not from the leg's start. Both
can be **infeasible** for a given leg (no path at all from the discovery
point to the destination); Stage 5 already showed real closures can and
do disconnect parts of the graph, so this had to be a first-class,
reportable outcome, not an exception to catch and hide.

**`T3` is anchored to `reactive`, not a free parameter.** The pre-Stage-0
scaffolding for `dlm.simulation.replan` already said the design: "detect
a blockage during execution and trigger re-optimisation... from the
vehicle's current position." Detecting something requires not already
knowing it — that's `reactive` by construction. Re-optimisation is what a
real dispatcher does the instant a driver reports being stuck; there is
nothing to trigger that from under `omniscient`, which never "discovers"
anything mid-route. If `reactive` never hits a closure at all (a slow
zone only, or a scenario that misses the route), `T3` is a no-op —
`triggered=False`, and its cost equals reactive `T2` exactly. This is
also what makes `T1 == T2 == T3` under a no-op disruption a meaningful
regression test rather than a special case in the code.

**`T3` uses a path-specific 2-opt objective.** The blockage node is the
fixed start, the real depot is the fixed endpoint, and only the not-yet-
served stops are reordered. The evaluated cost is therefore `blockage ->
remaining stops -> real depot`; the return home is jointly included rather
than appended after optimising a different closed tour. Feasibility
(can the blockage node, the depot, and every remaining stop all reach
each other?) is checked with a single strongly-connected-components pass
over the disrupted graph — the same idea Stage 5's own connectivity
checks already used, not a new concept.

**`T3_oracle` exists to answer "how much is lost by only reacting,"
and it genuinely surprised the numbers.** It's a from-scratch
`compute_t1`-style solve of the *whole* instance against the disrupted
graph — full knowledge before ever leaving the depot, free to reorder
every stop. In an exact solver this would always cost `<= T3`. It does
**not** always here, because both `T3_oracle` and `T3`'s sub-problem use
the same heuristic (nearest-neighbour + 2-opt): nearest-neighbour's
greedy first choice is sensitive to the exact cost matrix, so a
from-scratch solve on a slightly perturbed matrix can start down a
different, worse local optimum than 2-opt improving from the *original*
route's already-good order. The `small` instance under
`demo_single_edge_closure` measures exactly this (see Results) —
reported as a genuine finding about heuristic re-optimisation, not
smoothed into a false "more information is always better" claim.

**Service time depends on which stops were actually served.** For feasible
routes that complete the same deliveries it is identical across the metrics.
If T2 becomes infeasible, only completed deliveries are charged service time;
the failed leg and all later legs cannot be treated as completed work.

**Amendment to the glossary's anticipated design.** `docs/glossary.md`
(Stage 0) anticipated a three-valued `InformationModel` enum
(`omniscient`/`reactive`/`infeasible`). Implementation refined this:
`InformationModel` has two values (the two things a *driver* can know),
and infeasibility is a separate `feasible: bool` flag on
`T2Result`/`T3Result`, since either information model can turn out
infeasible — it isn't a third kind of knowledge, it's a possible outcome
of the other two. `docs/glossary.md` and `docs/modelling.md` are updated
to match, in the same spirit as Stage 4/5's transparent amendments to
earlier stages.

**Alternatives considered:**
- **`T3` re-optimising the whole route from the depot, unconditionally**
  (no "current position" concept at all) — this is materially what
  `T3_oracle` now is. Rejected as the definition of `T3` itself because
  the pre-committed `replan.py` scaffolding specifically describes a
  blockage-triggered, current-position re-optimisation; keeping both
  gives a realistic operating mode (`T3`) and a separate full-knowledge
  heuristic comparison (`T3_oracle`) instead of only one. Neither is a
  guaranteed bound because both are heuristic.
- **A closed tour back to the blockage point.** This was the original
  simplification, but it optimised the wrong endpoint. It was replaced by
  the explicit fixed-start/fixed-end path objective.

## Interfaces

- `dlm.simulation.execution`: `InformationModel`, `LegOutcome`,
  `BlockageInfo`, `ExecutionResult`, `execute_solution(graph, solution,
  information_model) -> ExecutionResult`.
- `dlm.simulation.replan`: `ReplanResult`, `replan_from_blockage(instance,
  solution, disrupted_graph, execution_result, solver=None) ->
  ReplanResult`.
- `dlm.simulation.metrics`: `T2Result`, `T3Result`, `T3OracleResult`,
  `compute_t2(instance, solution, disrupted_graph, information_model,
  default_service_time_s=None) -> T2Result`, `compute_t3(instance,
  solution, disrupted_graph, solver=None, default_service_time_s=None) ->
  T3Result`, `compute_t3_oracle(instance, disrupted_graph, solver=None,
  default_service_time_s=None) -> T3OracleResult`, `compute_saving(t2, t3)
  -> float | None`.
- CLI: `dlm compare --instance X --scenario Y [--solver nn_2opt|
  nearest_neighbour]`.

## Data & assumptions

- `T3`'s blockage trigger only fires on **closures** (a missing edge on
  the original path), never on slow zones alone — see Known limitations.
- Feasibility checks use strong connectivity on the disrupted graph, not
  pairwise `nx.has_path` — cheaper (one O(V+E) pass) and consistent with
  Stage 5's own connectivity evidence.
- Units as always: time in seconds, distance in metres.

## How to run

```bash
source .venv/bin/activate
dlm compare --instance small --scenario liffey_quays_closure
dlm compare --instance small --scenario luas_works_dawson_street
```

## Acceptance criteria

- ✅ **`T2` computed correctly for a route re-executed after a
  disruption, both `omniscient` and `reactive`.**
  `test_omniscient_beats_reactive_when_a_closure_forces_a_detour`: a
  hand-built graph where the true costs (34 vs 35) are derived by hand
  and cross-checked against the code's output exactly.
- ✅ **`T3` computed correctly for a re-optimised route.**
  `test_t3_replans_from_the_blockage_and_matches_hand_derived_cost`
  (offline, hand-derived) and
  `test_compare_pipeline_runs_end_to_end_for_every_curated_scenario`
  (real Dublin graph, all 4 curated scenarios).
- ✅ **Information model enum affects the computed cost as expected.**
  `test_closing_the_only_detour_makes_reactive_and_replan_infeasible_but_not_omniscient`
  and the real-graph
  `test_quays_closure_strands_reactive_but_not_omniscient_on_small_instance`
  — the same disruption, same route, feasible under one model and not the
  other.
- ✅ **`T1 == T2 == T3` regression test for a no-op disruption.**
  `test_t1_t2_t3_are_identical_under_a_no_op_disruption` (offline) and
  `test_t1_t2_t3_identical_on_real_graph_with_no_disruption` (real
  graph) — exactly the test the Stage 0 scaffolding for
  `tests/test_simulation.py` promised.
- ✅ **`Saving %` reported and correctly signed.** `compute_saving`
  tested directly against hand-derived `T2`/`T3` values, plus the
  infeasible-input case (`None`, not a crash or a nonsense percentage).
- ✅ **`dlm compare` runs end-to-end on a real instance + scenario and
  writes a self-describing run directory.** See Results below for a real
  transcript; `results/<instance>-vs-<scenario>-<timestamp>/{config.yaml,
  result.json, route_map.html, disruption_map.html}`.

All 113 tests pass (`pytest -v`: 96 from Stages 0-5 + 17 new this stage,
10 offline / 7 network-marked within it); `ruff check .` / `ruff format
--check .` clean.

## Results / evidence

`dlm compare --instance small --scenario <X>` against all four curated
scenarios, plus one narrow single-edge demo scenario
(`scenarios/demo_single_edge_closure.yaml`) built specifically to force a
*successful* local detour (the curated corridor/polygon closures are each
wide enough that the `small` instance's route either misses them entirely
or gets stranded deep inside — see the infeasible rows below; a single
edge is what makes a clean, non-infeasible detour example possible):

| Scenario | Edges closed/slowed | T1 | T2 (omni) | T2 (reactive) | T3 | T3_oracle | Saving % |
|---|---|---|---|---|---|---|---|
| `oconnell_street_protest` | 58/0 | 3260.0s | 3260.0s | 3260.0s | 3260.0s (not triggered) | 3260.0s | 0% (misses the route) |
| `luas_works_dawson_street` | 0/2 | 3260.0s | 3387.7s | 3387.7s | 3387.7s (not triggered) | 3378.1s | 0%* |
| `demo_single_edge_closure` | 1/0 | 3260.0s | 3262.8s | 3262.8s | 3262.8s (triggered) | 3364.4s | 0% |
| `liffey_quays_closure` | 44/0 | 3260.0s | 3420.5s | **infeasible** | **infeasible** | 3280.3s | n/a — recovered by full replan |
| `st_patricks_day_parade` | 53/0 | 3260.0s | **infeasible** | **infeasible** | **infeasible** | **infeasible** | n/a |

\* `luas_works_dawson_street` is a real, honest limitation, not a
rounding artefact: `T3_oracle` (3378.1s) is genuinely cheaper than `T2`
(3387.7s) — a full re-optimisation *would* find a better order — but `T3`
never gets the chance, because a slow zone never breaks the original
path and therefore never triggers `replan_from_blockage` (see Known
limitations).

Two genuinely interesting real findings, not manufactured for the report:

1. **`liffey_quays_closure` strands `reactive` but not `omniscient`, and
   `T3_oracle` recovers where `T3` cannot.** The `small` instance's
   `s5 -> s1` leg hits a closure reactive can find no detour from
   (infeasible); `omniscient`, given the same disruption but full
   knowledge from the leg's start, still finds a route (3420.5s).
   `T3`, anchored to the same stuck reactive position, is also
   infeasible — but `T3_oracle`, free to reorder *every* stop from the
   depot rather than continue from wherever `reactive` got stuck, finds
   an entirely different, fully feasible route (3280.3s, barely above
   `T1`). Reacting only after being blocked can strand a vehicle in a
   position a full replan would simply never have visited.
2. **`demo_single_edge_closure` measures `T3_oracle > T3`.** A from-scratch
   solve (`T3_oracle`, 3364.4s) lands in a worse nearest-neighbour local
   optimum than 2-opt improving from the original route's already-good
   order (`T3`, 3262.8s) — both are the same heuristic solver, so "more
   information, re-solved from scratch" is not a strict guarantee of a
   better answer. Documented in `dlm.simulation.metrics.T3OracleResult`
   and `docs/modelling.md`, not hidden.

`docs/report/compare_liffey_quays_disruption_map.png` — a real rendered
screenshot of this run's `disruption_map.html`, showing the 44 closed
edges (red) stretching along the north quays from O'Connell Bridge past
Talbot Memorial Bridge, following real street geometry.

```
$ dlm compare --instance small --scenario liffey_quays_closure
instance:          small  scenario: liffey_quays_closure
edges closed/slowed: 44/0
T1 (normal):       3260.0s
T2 (omniscient): 3420.5s (drive 1980.5s)
T2 (reactive)  : INFEASIBLE (a required leg has no path on the disrupted graph)
T3 (re-optimised): INFEASIBLE
T3 full-knowledge heuristic: 3280.3s
written to:        results/small-vs-liffey_quays_closure-<timestamp>/
```

## Known limitations

- **`T3`'s blockage trigger only fires on closures, never on slow
  zones alone.** A slow zone that doesn't break the original path never
  triggers `replan_from_blockage`, even when (as
  `luas_works_dawson_street` shows) a full re-optimisation would have
  found a cheaper order. Detecting "is a smarter order available" after
  *every* leg regardless of whether anything broke would require running
  a full re-optimisation constantly, a materially more expensive design
  not needed to compare "frozen plan" vs "reactive replan after a real
  blockage" at this project's scale.
- **`T3_oracle` is not a strict upper bound on `T3`** — see the
  `demo_single_edge_closure` finding above. Both use the same heuristic
  solver, so this only holds when the solver is exact, which
  `TwoOptSolver` is not.
- **`T3`'s remaining-stops sub-problem doesn't jointly optimise for a
  cheap final return leg to the depot** (see Design).
- Every `T2`/`T3` number here uses `information_model=reactive` as the
  headline comparison for `Saving %`; `omniscient` is reported
  side-by-side for context but isn't part of the `Saving %` formula
  itself — a modelling choice (the realistic "driver wasn't told in
  advance" baseline), not a technical constraint.

## Next

Stage 7 depends on:
- `dlm compare`'s run-directory convention (`config.yaml` naming exactly
  what produced each `result.json`) as what `dlm batch` will repeat many
  times over, one directory per (instance, scenario) pair.
- `compute_t2`/`compute_t3`/`compute_t3_oracle`/`compute_saving` as the
  per-run computation `dlm batch` aggregates across many scenarios —
  unchanged, just called in a loop.
- `dlm.disruption.generators` (still a stub) for seeded/stochastic
  scenario generation, so Stage 7 isn't limited to the 4 curated + 1 demo
  scenarios that exist today.

Stage 7 will build the results harness: batch experiment running,
aggregate tables/figures, and `make reproduce`.
