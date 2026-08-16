# Modelling

## Problem

A single vehicle (`K=1` until Stage 8) starts at a depot, must visit every
stop in an instance exactly once, and return to the depot — the classic
**Travelling Salesman Problem (TSP)**, on a real, directed, asymmetric road
network rather than a Euclidean plane.

## Objective function

Minimise total route time:

```
total_time_s(order) = sum over consecutive (u, v) in [depot, order[0], ..., order[-1], depot]
                       of matrix.get_cost(u, v)
```

where `matrix.get_cost(u, v)` is the Stage 3 travel-time matrix's shortest
driving time from `u` to `v` on the real Dublin graph. This is exactly
`Solution.total_time_s` (`dlm.solver.base`).

**This is pure driving time — not `T1`.** `T1` (`dlm.simulation.metrics.compute_t1`)
adds per-stop service time on top:

```
T1 = drive_time_s + service_time_s
   = Solution.total_time_s + sum(stop.service_time_s or default_service_time_s for stop in order)
```

This split is deliberate: the **solver** only ever minimises driving time
(service time doesn't depend on visit order — it's paid once per stop
regardless of when it's visited, so it can't change which order is
optimal). The **metrics** layer is where service time — and, from Stage 6,
lateness penalties — gets added to produce the headline number. Keeping
solvers ignorant of service time keeps them simple and reusable (the same
`Solver` protocol will serve the Stage 8 OR-Tools benchmark too) without
losing anything: adding a constant per-stop cost to every possible route
can never change which route minimises total driving time.

This answers the §9 ADR question directly: the objective is **travel time
+ service time**, not travel time alone, and not (yet) a lateness penalty
— that requires time windows, which don't exist until Stage 8.

## Assumptions

- **Service time**: `dlm.config.settings.default_service_time_s = 180.0`
  (3 minutes) is used whenever a stop's own `service_time_s` is 0 (its
  schema default since Stage 2 — no per-stop value has been set by any
  code yet). **This is a placeholder pending author confirmation** —
  raised as an ADR proposal in `docs/stages/stage-04-baseline.md`, per
  §9 of the project brief ("Service time per stop: fixed or
  size-dependent? What value, and on what basis?").
- **No lateness penalty, no time windows** — `Stop.time_window` exists in
  the schema (Stage 2) but is not read anywhere until Stage 8 (VRPTW).
- **Single vehicle** (`K=1`) — Stage 8 generalises to a fleet.
- **Free-flow travel times** — the objective uses Stage 1's static,
  free-flow (or imputed-default) speeds; no time-of-day or congestion
  modelling (deliberate, per ADR-0001: no live traffic APIs).

## Why Nearest-Neighbour + 2-opt

Fixed technical decision (ADR-0001), not re-litigated here — but the
*reasoning* is worth stating for the viva: a hand-implemented heuristic
that the authors can trace through by hand on a tiny example is more
defensible than a black-box solver for a "v1" primary method, and is
simple enough to unit-test against a manually-verified known optimum (see
`tests/test_solvers.py`). OR-Tools (Stage 8) exists specifically to
quantify what this heuristic gives up in solution quality, once there is
a baseline to compare it against.

**Nearest-neighbour construction** (`dlm.solver.nearest_neighbour`):
greedily extends the route by always stepping to whichever unvisited stop
is cheapest to reach from the current position, using the matrix's
*directed* cost — never a symmetrised distance. Runtime: `O(N^2)` (`N`
steps, each scanning up to `N` remaining candidates).

**2-opt improvement** (`dlm.solver.two_opt`): repeatedly looks for a pair
of positions whose segment reversal reduces total cost, applying the first
such improvement found (first-improvement, not best-improvement — simpler
to reason about, at the cost of possibly more iterations to converge).
Stops when no single reversal improves the route, or an iteration cap is
hit (`DEFAULT_MAX_ITERATIONS = 2000`).

**Directed-cost re-evaluation, not the symmetric O(1) delta.** The
standard 2-opt cost-delta trick (`new_cost = old_cost - d(a,b) - d(c,d) +
d(a,c) + d(b,d)`) assumes reversing a segment doesn't change any internal
edge's cost — true only when `d(x,y) == d(y,x)` everywhere. Stage 3
measured over 95% of point pairs asymmetric on real Dublin instances, so
that assumption does not hold here. `two_opt_improve` instead recomputes
the **entire** candidate route's cost (`route_time_s`, `O(N)`) for every
candidate reversal. This is the brief's "2-opt with correct directed cost
re-evaluation" option (the alternative offered was Or-opt/relocate, which
sidesteps the issue by never reversing a segment at all — rejected here
specifically because 2-opt is the fixed technical decision, and the
full-recomputation fix is simple enough not to need the alternative).

**Complexity.** One full improvement pass evaluates `O(N^2)` candidate
reversals, each costing `O(N)` to re-evaluate: `O(N^3)` per pass. At
`N <= 50` (the project's instance-size ceiling, Stage 2) that's at most
~125,000 cost lookups per pass — each an `O(1)` dictionary lookup into the
Stage 3 matrix — which is why measured solve times are milliseconds, not
seconds, even for the `large` (N=40) canonical instance (see
`docs/stages/stage-04-baseline.md` for measured numbers). This would not
scale to instances of hundreds or thousands of stops without either a
smarter neighbourhood search or the O(1) delta trick restricted to
provably-symmetric sub-regions — out of scope for this project's
Dublin-last-mile scale.

## T2, T3, and the information model

`T1` is the planned route's cost under normal conditions. Once a
disruption exists (Stage 5), "the same route's cost after the disruption"
is not a single obvious number — it depends on **what the driver of the
original planned route knows about the disruption, and when**:

- **`omniscient`**: the driver knows about the disruption before setting
  off, so every leg between two consecutive planned stops is driven along
  whatever the *disrupted* graph's shortest path actually is — the stop
  **order** never changes (that is `T3`'s job, not this), only the path
  taken between stops can.
- **`reactive`**: the driver only discovers a disruption by driving into
  it. They follow the *original* leg's planned path node by node; an
  edge that still exists (unaffected, or just a slow zone) is used at its
  current cost; the moment an edge no longer exists, that is where the
  disruption is *discovered*, and the driver detours from exactly that
  point — not from the leg's start — to the leg's original destination.

`T2` is computed under one of these (`dlm.simulation.execution
.execute_solution`, `InformationModel`), and can turn out **infeasible**
for either: no path exists from the discovery point to a leg's
destination at all. This is a real, reportable outcome (Stage 5 measured
that a full closure can and does disconnect parts of the real Dublin
graph), not an error.

```
T2 = drive_time(same order, executed under `information_model`) + service_time
```

`T3` is the cost after **re-optimising** the not-yet-served stops from
wherever a `reactive` execution first discovered a closure
(`dlm.simulation.replan.replan_from_blockage`) — deliberately anchored to
`reactive`, since re-optimisation is what a real dispatcher does the
moment a driver reports being blocked, and there is nothing to react to
under `omniscient` (it never "discovers" anything mid-route to trigger a
replan from). If the reactive execution never hits a closure at all (a
slow zone only, or a disruption that misses the route entirely), `T3`
trivially equals the reactive `T2` — nothing to re-optimise around.

```
T3 = drive_time(served prefix, actual)
   + drive_time(re-optimised order over the remaining stops, from the
                blockage point, on the disrupted graph)
   + drive_time(last remaining stop -> true depot)
   + service_time
Saving(%) = (T2 - T3) / T2 * 100
```

**Service time is identical across `T1`/`T2`/`T3`.** A disruption changes
how long it takes to *drive between* stops; it never changes how long you
spend *at* one (Stage 4's principle, unchanged here) — so
`_total_service_time_s` is computed once and reused by all three, and
only `drive_time_s` can differ between them.

**`T3_oracle`** (`dlm.simulation.metrics.compute_t3_oracle`) is a fourth
number, not part of the `Saving %` formula: a from-scratch re-solve of
the *whole* instance (every stop, not just the not-yet-served ones)
against the disrupted graph, as if the disruption had been known before
ever leaving the depot — exactly `compute_t1`, run on `disrupted_graph`
instead of the normal graph. It exists to ask "how much is lost by only
reacting once actually blocked, versus knowing from the start," and in
an *exact* solver it would always be `<= T3`. **It is not always `<= T3`
here**, because both are solved with the same heuristic (`TwoOptSolver`):
nearest-neighbour's greedy first choice is sensitive to the exact cost
matrix, so a from-scratch solve on a slightly different (disrupted)
matrix can land in a worse local optimum than 2-opt reaching from the
*original* route's already-good order. This is a genuine measured
outcome (see `docs/stages/stage-06-experiment.md`'s Results), reported
honestly rather than forced to fit the "more information is always
better" intuition that only strictly holds for an exact solver.

**Why `T3`'s re-optimisation doesn't jointly plan for a cheap final
return leg.** The remaining-stops sub-problem is solved as an ordinary
closed tour starting and ending at the blockage node (reusing
`TwoOptSolver` unchanged — no solver code needed to change for this
stage), then one direct leg from the last stop visited to the true depot
is appended. This does not jointly optimise for *which* stop should be
last so that final leg is cheap — a documented simplification (see
`docs/stages/stage-06-experiment.md`), not a silent one, chosen because
it reuses Stage 4's solver with zero modification at the cost of a
potentially-suboptimal final leg, which is simple enough to explain and
correct enough at this project's scale (`N <= 50`).

### Worked example

A small hand-built graph (`tests/test_simulation.py`'s "diamond" fixture)
makes every number checkable by hand:

```
      10        10        10
  0 ------> 1 ------> 2 ------> 3
             \                  ^
              12                |
               \                12
                v               |
                4 ---------------
                 \--3--> 2   (shortcut back from 4 to 2)
  3 ------> 0   (return leg, 20)
```

A single stop `A` sits at node 3. Under normal conditions the solver
picks `0-1-2-3` (cost 30) over `0-1-4-3` (34) or `0-1-2-4-3` (35) — the
direct path is genuinely fastest, so `T1 = 30 + 20 = 50` (+ service time).

Close edge `2->3` (a single closure, nothing else):

- **omniscient**: fresh shortest path `0->3` on the disrupted graph is
  `0-1-4-3 = 34` — it never goes near node 2 at all. `T2(omniscient)`
  drive time `= 34 + 20 = 54`.
- **reactive**: drives `0-1-2 = 20`, discovers `2->3` is gone, detours
  `2-4-3 = 3+12 = 15` from node 2. `T2(reactive)` drive time
  `= 20 + 15 + 20 = 55` — one unit worse than omniscient, exactly the
  cost of the "wasted" trip into node 2 before discovering the closure.
- **T3**: replan triggers from node 2 (where the blockage was found);
  with only one stop left to serve there is nothing to reorder, so `T3`
  matches `T2(reactive)` exactly: drive time `55`, `Saving % = 0`.

Now also close edge `2->4` (removing reactive's only detour): `reactive`
gets stuck at node 2 with no way to node 3 at all — `T2(reactive)` and
`T3` are both **infeasible** — while `omniscient` is entirely unaffected
(`54`, unchanged), since it never routes through node 2 to begin with.
This is the concrete case the `omniscient`/`reactive` distinction exists
to capture, and it is verified exactly as stated in
`tests/test_simulation.py::test_closing_the_only_detour_makes_reactive_and_replan_infeasible_but_not_omniscient`.

Real-Dublin evidence for both the divergence and the infeasibility cases
is in `docs/stages/stage-06-experiment.md`.

## Multi-vehicle CVRP and the OR-Tools benchmark

Everything above assumes a single vehicle (`K=1`). `instance.fleet_size`
(`K`) and `instance.vehicle_capacity` have existed in the schema since
Stage 2, unused until now: `fleet_size > 1` turns the problem into a
**Capacitated Vehicle Routing Problem (CVRP)** — `K` vehicles, each
starting and ending at the depot, jointly visiting every stop exactly
once, with each vehicle's total picked-up `Stop.demand` never exceeding
`vehicle_capacity`.

**Clarke-Wright savings** (`dlm.solver.clarke_wright`) is the
hand-implemented method: every stop starts on its own trivial round trip;
routes are greedily merged in descending order of

```
savings(i, j) = cost(i, depot) + cost(depot, j) - cost(i, j)
```

— the driving time saved by joining the route ending at `i` directly to
the route starting at `j`, instead of both returning to the depot and
setting off again — skipping any merge that would exceed
`vehicle_capacity`. Unlike Stage 4's 2-opt, this formula needs no
adaptation for Dublin's directed, asymmetric graph: every term already
uses its one natural direction. Each resulting route is then handed to
the *same* `two_opt_improve` Stage 4 already built, unchanged — a single
vehicle's route is exactly the single-vehicle TSP that function solves.

**Fleet-size capping.** If capacity-respecting merging still leaves more
routes than `K`, the largest (most-stops) routes are kept and the rest's
stops are reported in `FleetSolution.unassigned` — maximising stops
served given a fixed vehicle budget, honestly reporting what didn't fit
rather than silently dropping it or violating capacity to force
everything in.

**OR-Tools (`dlm.solver.ortools_solver`) is the benchmark oracle**
(ADR-0001), not a second primary method: one `RoutingModel` setup handles
`K=1` and `K>1` alike (a capacity dimension when `vehicle_capacity` is
set, a disjunction-with-penalty per stop so an infeasible instance drops
the least-costly-to-drop stops instead of returning no solution at all),
solved with guided local search under a time budget. `dlm benchmark`
runs both solvers on the same instance and reports the gap.

**Time windows are OR-Tools-only.** `Stop.time_window` has existed since
Stage 2; a time-window-respecting 2-opt/Clarke-Wright would need to
re-check every downstream stop's schedule after each candidate move —
substantially more engineering than this project's hand-implemented v1
scope justifies. OR-Tools solves VRPTW natively (one more dimension), so
the benchmark oracle demonstrates it directly (`dlm.solver.ortools_solver
.OrToolsSolver.solve_fleet(..., apply_time_windows=True)`) — exactly the
kind of capability gap a benchmark exists to make visible, per
`docs/stages/stage-08-fleet-benchmark.md`'s real evidence.
