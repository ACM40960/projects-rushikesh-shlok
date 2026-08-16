"""`T1`/`T2`/`T3`/`Saving %`: the project's headline evaluation metrics.

`T1` (Stage 4) is the planned route's cost under normal conditions. `T2`
(this stage) is the cost of driving that *same* planned route once a
disruption exists, under an explicit information model
(`dlm.simulation.execution`). `T3` (this stage) is the cost after
re-optimising from wherever a `reactive` execution first got blocked
(`dlm.simulation.replan`). `Saving %` compares the two:

```
Saving(%) = (T2 - T3) / T2 * 100
```

All three share one invariant: **service time never depends on routing**
(Stage 4's `compute_t1` docstring) — a disruption changes how long it
takes to *drive between* stops, never how long you spend *at* one. So
`T1.service_time_s == T2.service_time_s == T3.service_time_s` always,
computed once by `_total_service_time_s` and reused by all three; only
`drive_time_s` (and therefore `total_time_s`) can differ.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import networkx as nx

from dlm.config import settings
from dlm.instance.matrix import DEFAULT_WEIGHT, _build
from dlm.instance.schema import Instance
from dlm.simulation.execution import InformationModel, execute_solution
from dlm.simulation.replan import _strongly_connected_together, replan_from_blockage
from dlm.solver.base import FleetSolution, Solution, Solver
from dlm.solver.two_opt import TwoOptSolver


@dataclass(frozen=True)
class LegMetric:
    """One leg's contribution to the route, for the per-leg report table."""

    from_id: str
    to_id: str
    travel_time_s: float
    distance_m: float


@dataclass(frozen=True)
class T1Result:
    """`T1`: cost of the planned route under normal conditions.

    Attributes
    ----------
    drive_time_s : float
        Sum of leg travel times — identical to `Solution.total_time_s`,
        duplicated here so `T1Result` is self-contained for reporting.
    service_time_s : float
        Sum of time spent at each visited stop (the depot itself has no
        service time). A stop's own `service_time_s` is used if it is
        non-zero; otherwise `default_service_time_s` (settings, or the
        value passed to `compute_t1`) is assumed — see
        docs/stages/stage-04-baseline.md for why this convention was
        chosen and the open question it's raised as an ADR proposal.
    total_time_s : float
        `drive_time_s + service_time_s` — the headline `T1` number.
    distance_m : float
        Sum of leg distances — identical to `Solution.total_distance_m`.
    n_stops_served : int
        Number of stops visited (always all of them for `T1`; the
        distinction matters from Stage 6, where a disruption can leave
        some unreachable).
    legs : list[LegMetric]
        Per-leg breakdown, depot-to-depot.
    """

    drive_time_s: float
    service_time_s: float
    total_time_s: float
    distance_m: float
    n_stops_served: int
    legs: list[LegMetric] = field(default_factory=list)


def _total_service_time_s(
    instance: Instance, order: list[str], default_service_time_s: float | None
) -> float:
    """Sum of time spent at every stop in `order` — the one piece of
    `T1`/`T2`/`T3` that never depends on the disruption or the route
    taken between stops, only on which stops are visited (always all of
    them, for every one of `T1`/`T2`/`T3` as currently defined — Stage 8's
    fleet/capacity work is what could leave some unvisited)."""
    fallback = (
        default_service_time_s
        if default_service_time_s is not None
        else settings.default_service_time_s
    )
    stops_by_id = {s.id: s for s in instance.stops}
    return sum((stops_by_id[stop_id].service_time_s or fallback) for stop_id in order)


def compute_t1(
    instance: Instance,
    solution: Solution,
    default_service_time_s: float | None = None,
) -> T1Result:
    """Compute `T1` for a planned route under normal conditions.

    Parameters
    ----------
    instance : Instance
        Must have the same stops `solution.order` references.
    solution : Solution
        A solved route (e.g. from `TwoOptSolver`).
    default_service_time_s : float, optional
        Fallback per-stop service time when a stop's own is 0 (unset).
        Defaults to `settings.default_service_time_s`.
    """
    service_time_s = _total_service_time_s(instance, solution.order, default_service_time_s)

    return T1Result(
        drive_time_s=solution.total_time_s,
        service_time_s=service_time_s,
        total_time_s=solution.total_time_s + service_time_s,
        distance_m=solution.total_distance_m,
        n_stops_served=len(solution.order),
        legs=[
            LegMetric(
                from_id=leg.from_id,
                to_id=leg.to_id,
                travel_time_s=leg.travel_time_s,
                distance_m=leg.distance_m,
            )
            for leg in solution.legs
        ],
    )


@dataclass(frozen=True)
class FleetT1Result:
    """`T1` for a `FleetSolution` (Stage 8, `fleet_size > 1`): every
    vehicle's own `T1Result`, plus fleet-wide totals.

    Attributes
    ----------
    per_vehicle : list[T1Result]
        One `T1Result` per vehicle actually used — `compute_t1` applied
        unchanged to each vehicle's own `Solution`.
    n_stops_unassigned : int
        Stops no vehicle could serve (`FleetSolution.unassigned`) — not
        counted in any total below; a fleet-wide `T1` that silently
        ignored them would understate the instance's real cost.
    """

    drive_time_s: float
    service_time_s: float
    total_time_s: float
    distance_m: float
    n_stops_served: int
    n_stops_unassigned: int
    per_vehicle: list[T1Result] = field(default_factory=list)


def compute_fleet_t1(
    instance: Instance,
    fleet: FleetSolution,
    default_service_time_s: float | None = None,
) -> FleetT1Result:
    """Compute `T1` for a multi-vehicle `FleetSolution` — sums each
    vehicle's own `compute_t1` (each vehicle's `Solution.order` only ever
    contains that vehicle's stops, so there's no double-counting)."""
    per_vehicle = [compute_t1(instance, s, default_service_time_s) for s in fleet.routes]
    drive_time_s = sum(t1.drive_time_s for t1 in per_vehicle)
    service_time_s = sum(t1.service_time_s for t1 in per_vehicle)
    return FleetT1Result(
        drive_time_s=drive_time_s,
        service_time_s=service_time_s,
        total_time_s=drive_time_s + service_time_s,
        distance_m=sum(t1.distance_m for t1 in per_vehicle),
        n_stops_served=sum(t1.n_stops_served for t1 in per_vehicle),
        n_stops_unassigned=len(fleet.unassigned),
        per_vehicle=per_vehicle,
    )


@dataclass(frozen=True)
class T2Result:
    """`T2`: cost of driving the *same* planned route once a disruption
    exists, under an explicit `InformationModel`.

    Attributes
    ----------
    information_model : InformationModel
        Which model produced `drive_time_s` — `omniscient` or `reactive`.
    feasible : bool
        `False` if some leg has no path at all from where the disruption
        was discovered to that leg's destination on the disrupted graph —
        `drive_time_s`/`total_time_s`/`distance_m` are `None` in that case
        (there is no finite `T2` to report, not a zero or an error).
    """

    information_model: InformationModel
    feasible: bool
    drive_time_s: float | None
    service_time_s: float
    total_time_s: float | None
    distance_m: float | None
    n_stops_served: int
    legs: list[LegMetric] = field(default_factory=list)


def compute_t2(
    instance: Instance,
    solution: Solution,
    disrupted_graph: nx.MultiDiGraph,
    information_model: InformationModel = InformationModel.REACTIVE,
    default_service_time_s: float | None = None,
) -> T2Result:
    """Compute `T2`: drive `solution` (unchanged stop order) over
    `disrupted_graph` under `information_model`.

    Parameters
    ----------
    disrupted_graph : nx.MultiDiGraph
        E.g. `dlm.disruption.engine.DisruptionResult.graph`.
    information_model : InformationModel, default `reactive`
        See `dlm.simulation.execution` for what each model means.
    """
    execution = execute_solution(disrupted_graph, solution, information_model)
    service_time_s = _total_service_time_s(instance, solution.order, default_service_time_s)

    drive_time_s = execution.drive_time_s
    total_time_s = drive_time_s + service_time_s if execution.feasible else None

    return T2Result(
        information_model=information_model,
        feasible=execution.feasible,
        drive_time_s=drive_time_s,
        service_time_s=service_time_s,
        total_time_s=total_time_s,
        distance_m=execution.distance_m,
        n_stops_served=len(solution.order),
        legs=[LegMetric(o.from_id, o.to_id, o.travel_time_s, o.distance_m) for o in execution.legs],
    )


@dataclass(frozen=True)
class T3Result:
    """`T3`: cost after re-optimising the not-yet-served stops from
    wherever a `reactive` execution first got blocked
    (`dlm.simulation.replan.replan_from_blockage`).

    Attributes
    ----------
    triggered : bool
        `False` if the reactive execution never hit a closure — `T3`
        equals the reactive `T2` exactly in that case (nothing to
        re-optimise around).
    feasible : bool
        `False` if the blockage node cannot reach every remaining stop and
        the depot on the disrupted graph.
    order : list[str]
        The stop order actually driven: served prefix + re-optimised
        remainder (or the original order, if `triggered` is `False`).
    """

    triggered: bool
    feasible: bool
    drive_time_s: float | None
    service_time_s: float
    total_time_s: float | None
    distance_m: float | None
    order: list[str]


def compute_t3(
    instance: Instance,
    solution: Solution,
    disrupted_graph: nx.MultiDiGraph,
    solver: Solver | None = None,
    default_service_time_s: float | None = None,
) -> T3Result:
    """Compute `T3`: re-optimise from wherever a `reactive` execution of
    `solution` first got blocked by a closure, and drive the rest.
    """
    execution = execute_solution(disrupted_graph, solution, InformationModel.REACTIVE)
    replan = replan_from_blockage(instance, solution, disrupted_graph, execution, solver=solver)
    service_time_s = _total_service_time_s(instance, solution.order, default_service_time_s)

    total_time_s = (
        replan.drive_time_s + service_time_s
        if replan.feasible and replan.drive_time_s is not None
        else None
    )

    return T3Result(
        triggered=replan.triggered,
        feasible=replan.feasible,
        drive_time_s=replan.drive_time_s,
        service_time_s=service_time_s,
        total_time_s=total_time_s,
        distance_m=replan.distance_m,
        order=replan.order,
    )


@dataclass(frozen=True)
class T3OracleResult:
    """`T3_oracle`: what a full-knowledge re-optimisation *from scratch*
    finds — full knowledge of the disruption *before ever leaving the
    depot*, free to reorder every stop (not just the not-yet-served
    ones), solved fresh against the disrupted graph.

    This is not a realistic operating mode: no "current position" or
    blockage-discovery event is involved at all — it is exactly
    `compute_t1`, run against `disrupted_graph` instead of the normal
    graph. It exists to ask "how much is lost by only reacting once
    actually blocked, versus knowing from the start" —

    **`T3_oracle <= T3` would always hold for an exact solver, but does
    not always hold here.** Both `T3_oracle` and `T3`'s remaining-stops
    sub-problem are solved with the same heuristic (`TwoOptSolver`:
    nearest-neighbour + 2-opt, not a global optimum). Nearest-neighbour's
    greedy first choice is sensitive to the exact cost matrix, so a fresh
    solve on a slightly different (disrupted) matrix can start down a
    different, worse local optimum than 2-opt reaching from the
    *original* route's already-good order — a genuine, measured outcome
    (`docs/stages/stage-06-experiment.md`), not a bug being explained
    away. `T3_oracle` should be read as "roughly how good a from-scratch
    replan can be," not a strict upper bound, while both solvers are
    heuristic.
    """

    feasible: bool
    drive_time_s: float | None
    service_time_s: float
    total_time_s: float | None
    distance_m: float | None
    order: list[str]


def compute_t3_oracle(
    instance: Instance,
    disrupted_graph: nx.MultiDiGraph,
    solver: Solver | None = None,
    default_service_time_s: float | None = None,
) -> T3OracleResult:
    """Compute `T3_oracle`: re-solve the whole instance from scratch
    against `disrupted_graph`, as if the disruption had been known before
    departure.
    """
    solver = solver or TwoOptSolver()
    points = [instance.depot.node, *(s.node for s in instance.stops)]
    service_time_s = _total_service_time_s(
        instance, [s.id for s in instance.stops], default_service_time_s
    )

    if not _strongly_connected_together(disrupted_graph, points):
        return T3OracleResult(
            feasible=False,
            drive_time_s=None,
            service_time_s=service_time_s,
            total_time_s=None,
            distance_m=None,
            order=[],
        )

    matrix = _build(disrupted_graph, points, DEFAULT_WEIGHT)
    solution = solver.solve(instance, matrix)

    return T3OracleResult(
        feasible=True,
        drive_time_s=solution.total_time_s,
        service_time_s=service_time_s,
        total_time_s=solution.total_time_s + service_time_s,
        distance_m=solution.total_distance_m,
        order=solution.order,
    )


def compute_saving(t2: T2Result, t3: T3Result) -> float | None:
    """`Saving(%) = (T2 - T3) / T2 * 100`.

    `None` if either side is infeasible or `T2.total_time_s` is 0 — there
    is no percentage to report (see `dlm.simulation.metrics` callers for
    how an infeasible `T2` recovered by `T3` is reported instead: that is
    the most interesting outcome a `None` here can hide, so callers should
    check `t2.feasible`/`t3.feasible` directly rather than only this
    return value).
    """
    if not (t2.feasible and t3.feasible):
        return None
    if not t2.total_time_s:
        return None
    return (t2.total_time_s - t3.total_time_s) / t2.total_time_s * 100


__all__ = [
    "FleetT1Result",
    "LegMetric",
    "T1Result",
    "T2Result",
    "T3OracleResult",
    "T3Result",
    "compute_fleet_t1",
    "compute_saving",
    "compute_t1",
    "compute_t2",
    "compute_t3",
    "compute_t3_oracle",
]
