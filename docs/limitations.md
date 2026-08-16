# Limitations

The honest, consolidated list of what this system does not model, pulled
from the "Known limitations" section of every stage doc (`docs/stages/`)
as of Stage 9. Nothing here is new — it is a single place to read what was
already disclosed stage by stage, organised by theme instead of by when it
was discovered. See the cited stage doc for full context on any item.

## Road network and travel times (Stage 1)

- No turn restrictions, no signal/junction delay, no live traffic — travel
  times are free-flow-only, computed from distance and an OSM `maxspeed`
  (imputed from a road-class default where `maxspeed` is absent). See
  `docs/data.md` for the full provenance and imputation table.
- `DEFAULT_BBOX` excludes the M50 and outer suburbs; a stop placed outside
  it fails to snap with a `SnapError` rather than crashing, but it is a
  real functional boundary (ADR-0003).
- The graph-fetch path (`_fetch_overpass_xml`) depends on `curl` being on
  `PATH` — a reasonable assumption for this project's dev/CI/report
  environment, but a portability constraint worth naming.
- No hand-checked route in this project has a verified live-traffic
  reference number to compare against; flagged rather than fabricated.

## Instances and geocoding (Stage 2)

- Ambiguity detection for geocoded presets (>500m + ≥50% relative
  importance) is a heuristic, not a guarantee.
- `InstanceBuilder.build()`'s reachability guarantee is structural (every
  node comes from Stage 1's largest strongly connected component), not
  re-verified pairwise per instance, and it stops being true once a
  *disrupted* graph view exists — which is exactly why Stage 5 owns its
  own first-class connectivity check.
- Duplicate stop-stop locations ("same node") are a warning, not an
  error; there is no interactive merge flow.

## Travel-time matrix (Stage 3)

- The matrix cache under `data/cache/matrix/` is unbounded — nothing
  prunes it. Not a problem at this project's scale (instances ≤50 points,
  each cached matrix at most a few MB), but worth noting for a
  long-running deployment.
- `asymmetry_rate` and `triangle_violations` are computed in `O(N²)`/
  `O(N³)` over the matrix's own points, trivial at `N<=50` but not
  something that would scale past a few hundred stops unchanged.

## Baseline solver (Stage 4)

- 2-opt is first-improvement, not best-improvement — may take marginally
  more iterations to converge, though not measurably at this project's
  scale (all canonical instances solve in well under a second).
- `default_service_time_s` (180s) is a single global constant, not
  category-dependent — a hospital stop and a residential drop-off are
  assumed equally quick (ADR-0004, with Stage 7's sensitivity sweep
  attached).
- No lateness penalty or time windows in the hand-implemented solver
  (VRPTW support exists only via the OR-Tools benchmark oracle, Stage 8 —
  a deliberate scope boundary, not a gap).

## Disruptions (Stage 5)

- Closure-beats-slow-zone / first-wins conflict resolution is simple, not
  physically modelled: two overlapping slow zones collapse to "only the
  first counts" rather than "the more severe of the two."
- Polygon/corridor disruption geometry is always resolved against the
  *undisrupted* graph — one disruption's geometry never depends on
  another's effect. Disruptions affect travel cost and reachability,
  never each other's own geometry resolution.
- `time_window`/`at_time` are implemented and tested but weren't driven
  by a real simulated clock until Stage 6 existed.

## Information model, T2/T3 (Stage 6)

- `T3`'s blockage trigger only fires on closures, never on slow zones
  alone — a slow zone that doesn't break the original path never
  triggers a re-plan, even when a full re-optimisation would have found a
  cheaper order. Detecting "is a smarter order available" after every leg
  regardless of whether anything broke would require re-optimising
  constantly, a materially more expensive design not needed to compare
  "frozen plan" vs. "reactive replan after a real blockage" at this
  project's scale.
- `T3_oracle` is **not** a strict upper bound on `T3` — both use the same
  heuristic solver (`TwoOptSolver`), so a from-scratch solve can land in a
  worse local optimum than one anchored to an already-good order. Measured
  directly (`demo_single_edge_closure` on `small`): `T3_oracle` came out
  worse than `T3`. This only holds because the solver is heuristic, not
  exact — reported honestly rather than forced into a false invariant.
- `T3`'s remaining-stops re-plan doesn't jointly optimise for a cheap
  final return leg to the depot.
- `Saving %`'s headline number always uses `information_model=reactive`
  (the realistic "driver wasn't told in advance" baseline); `omniscient`
  is reported for context only.

## Batch experiments and sensitivity (Stage 7)

- Uniformly-random disruption scenarios essentially never hit a route at
  this project's scale — measured directly: 0 of 30 random scenarios in
  the default batch affected any of the 3 canonical single-vehicle
  instances' `T2`/`T3`. A route touches perhaps 50-100 of the graph's
  ~62,000 edges, so a uniformly-placed random disruption has a
  correspondingly small chance of landing on one. The curated,
  real-world-anchored scenario library remains the source of every
  non-trivial `T2`/`T3` result in this project; a generator weighted
  toward a route's own stops/corridors would raise the hit rate but would
  also be a different, less representative experiment.
- `dlm batch` recomputes each scenario's disrupted graph from scratch
  (no cross-scenario caching) — fine at this project's scale, would need
  revisiting for a batch of hundreds of scenarios.
- `dlm sensitivity` only sweeps `T1`, not `T2`/`T3` — service time is
  identical across all three (Stage 6's invariant), so a `T1` sweep
  already answers the question completely.

## Fleet and benchmark (Stage 8)

- Clarke-Wright's fleet-size capping is a greedy heuristic, not provably
  optimal coverage under joint reordering. At this project's scale
  (`N<=50`) the measured effect is at most one stop's difference between
  Clarke-Wright's and OR-Tools' choice of which stops to drop.
- **No fleet-aware `T2`/`T3`.** A disrupted multi-vehicle re-plan (which
  vehicle should absorb a blocked stop, whether routes need rebalancing)
  is a materially different problem from Stage 6's single-route
  `execute_solution`/`replan_from_blockage`, and is not attempted here —
  the fleet's disruption behaviour is untested territory.
- `dlm benchmark`'s reported OR-Tools runtime is dominated by its
  configured time budget, not solution difficulty — it reflects the
  budget chosen (8-10s throughout this project), not the problem's true
  difficulty.

## Project-wide, cross-cutting

- **Single-day horizon.** There is no multi-day planning, no
  end-of-shift/driver-hours modelling, and no carry-over of undelivered
  stops to a next day.
- **Static demand and service times.** Delivery demand and service
  duration are both fixed inputs, not drawn from any observed
  distribution or updated during a run.
- **No live traffic feed.** Every travel time in this project — normal or
  disrupted — is a static, precomputed number; nothing here connects to a
  real-time data source, by design (the whole point is to isolate the
  effect of *disruptions the user specifies*, not to also model ambient
  traffic variance).
- **Single geographic area.** Everything is scoped to `DEFAULT_BBOX`
  (central Dublin); the project has not been run against, and makes no
  claim about, any other city's road network.

## What's out of scope, not merely undone

A few items above sound like gaps but are documented, deliberate scope
boundaries rather than missing work: VRPTW is OR-Tools-only by design
(Stage 8); fleet-aware `T2`/`T3` was never attempted, not attempted-and-
failed; and the random-scenario 0%-hit-rate finding (Stage 7) is reported
as a real result, not patched away by biasing the generator toward
guaranteed hits.
