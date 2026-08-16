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

## T1/T2/T3 (preview)

Only `T1` exists as of Stage 4. `T2` (the planned route's cost after a
disruption), `T3` (the re-optimised route's cost), `T3_oracle`, and
`Saving %` are Stage 6 additions, once a disruption (Stage 5) and a
re-optimisation trigger exist to define them against. This section will be
extended then with the full definitions, the information-model enum
(`omniscient`/`reactive`/`infeasible`), and a worked numeric example, per
the brief.
