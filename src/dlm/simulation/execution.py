"""Drive a ``Solution`` over a graph edge by edge under an explicit
information model: ``omniscient`` or ``reactive`` (the default).

This is what makes ``T2`` — "cost of the same planned route after the
disruption" — well-defined rather than a single obvious number: a planned
route is a fixed *order* of stops, but "driving it" under a disruption
depends on what the driver knows and when.

- **omniscient**: the driver knows about the disruption before setting
  off, so each leg is planned fresh against the disrupted graph (still in
  the *same stop order* — only the path between consecutive stops can
  change, never which stop comes next; reordering the whole route is
  ``replan``'s job, Stage 6's ``T3``, not this).
- **reactive**: the driver only discovers a disruption by driving into it.
  They follow the planned route's *original* path node by node; if an
  edge on it still exists (unaffected, or just slower — a slow zone), they
  use it at its current cost; the moment an edge no longer exists (a
  closure), that is where the disruption is *discovered*, and the driver
  detours from exactly that point to the leg's original destination via
  the disrupted graph's shortest path.

Either model can turn out **infeasible** for a given leg: no path exists
from the discovery point to the leg's destination at all. This is a real,
reportable outcome (see ``dlm.simulation.metrics.T2Result``), not an
error — a full corridor/area closure can and does disconnect parts of the
real Dublin graph (Stage 5's evidence).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

import networkx as nx

from dlm.instance.matrix import _path_distance_m
from dlm.solver.base import Leg, Solution


class InformationModel(StrEnum):
    """What the driver of the *original* planned route knows about a
    disruption, and when — see the module docstring."""

    OMNISCIENT = "omniscient"
    REACTIVE = "reactive"


@dataclass(frozen=True)
class LegOutcome:
    """One leg's outcome when `solution` is driven under a disruption."""

    from_id: str
    to_id: str
    travel_time_s: float | None
    """`None` if this leg is infeasible (no path from the discovery point
    to the leg's destination on the disrupted graph)."""
    distance_m: float | None
    detoured: bool
    """`True` if the driven path differs from `Leg.path` — always `False`
    for `omniscient` unless the disrupted shortest path genuinely differs
    from the original one; for `reactive`, `True` only when a closure was
    actually hit partway along the original path."""
    feasible: bool


@dataclass(frozen=True)
class BlockageInfo:
    """Where a `reactive` execution first discovered a closure it could
    not route around by simply continuing — the trigger point
    `dlm.simulation.replan` re-optimises from.
    """

    leg_index: int
    """Index into `solution.order`/`solution.legs` of the leg the
    blockage was discovered in."""
    node: int
    """The graph node the vehicle had actually reached when it discovered
    the closure — partway along the leg's original path, not necessarily
    the leg's start."""
    partial_time_s: float
    """Time already spent driving into this leg, before the closure."""
    partial_distance_m: float


@dataclass(frozen=True)
class ExecutionResult:
    """The outcome of driving a whole `Solution` under one information
    model."""

    information_model: InformationModel
    feasible: bool
    drive_time_s: float | None
    distance_m: float | None
    legs: list[LegOutcome]
    first_blockage: BlockageInfo | None
    """Set only for `reactive` executions that hit at least one closure
    partway along a leg's original path. `None` for `omniscient` (it never
    "hits" anything — every leg is planned fresh) and for `reactive`
    executions that never encounter a closure."""


def _leg_cost(graph: nx.MultiDiGraph, path: list[int]) -> tuple[float, float]:
    """Sum `travel_time`/distance along a node path on `graph`."""
    time_s = 0.0
    for u, v in zip(path[:-1], path[1:], strict=True):
        edge = min(graph[u][v].values(), key=lambda d: d["travel_time"])
        time_s += edge["travel_time"]
    return time_s, _path_distance_m(graph, path)


def _execute_leg_omniscient(graph: nx.MultiDiGraph, leg: Leg) -> LegOutcome:
    try:
        path = nx.shortest_path(graph, leg.from_node, leg.to_node, weight="travel_time")
    except nx.NetworkXNoPath:
        return LegOutcome(leg.from_id, leg.to_id, None, None, detoured=False, feasible=False)
    time_s, dist_m = _leg_cost(graph, path)
    return LegOutcome(
        leg.from_id, leg.to_id, time_s, dist_m, detoured=(path != leg.path), feasible=True
    )


def _execute_leg_reactive(
    graph: nx.MultiDiGraph, leg: Leg, leg_index: int
) -> tuple[LegOutcome, BlockageInfo | None]:
    traveled_time = 0.0
    traveled_dist = 0.0
    for u, v in zip(leg.path[:-1], leg.path[1:], strict=True):
        if graph.has_edge(u, v):
            edge = min(graph[u][v].values(), key=lambda d: d["travel_time"])
            traveled_time += edge["travel_time"]
            traveled_dist += edge["length"]
            continue

        blockage = BlockageInfo(leg_index, u, traveled_time, traveled_dist)
        try:
            detour_path = nx.shortest_path(graph, u, leg.to_node, weight="travel_time")
        except nx.NetworkXNoPath:
            return (
                LegOutcome(leg.from_id, leg.to_id, None, None, detoured=True, feasible=False),
                blockage,
            )
        detour_time, detour_dist = _leg_cost(graph, detour_path)
        outcome = LegOutcome(
            leg.from_id,
            leg.to_id,
            traveled_time + detour_time,
            traveled_dist + detour_dist,
            detoured=True,
            feasible=True,
        )
        return outcome, blockage

    # walked the whole original path (possibly slower, never blocked)
    return (
        LegOutcome(
            leg.from_id, leg.to_id, traveled_time, traveled_dist, detoured=False, feasible=True
        ),
        None,
    )


def execute_solution(
    graph: nx.MultiDiGraph,
    solution: Solution,
    information_model: InformationModel = InformationModel.REACTIVE,
) -> ExecutionResult:
    """Drive `solution` over `graph` (a disrupted view — see
    `dlm.disruption.engine.DisruptionResult.graph`) under
    `information_model`. `solution`'s own stop order is never changed —
    see the module docstring for what `omniscient`/`reactive` do differ
    on.
    """
    legs: list[LegOutcome] = []
    first_blockage: BlockageInfo | None = None

    for i, leg in enumerate(solution.legs):
        if information_model is InformationModel.OMNISCIENT:
            outcome = _execute_leg_omniscient(graph, leg)
        else:
            outcome, blockage = _execute_leg_reactive(graph, leg, i)
            if blockage is not None and first_blockage is None:
                first_blockage = blockage
        legs.append(outcome)

    feasible = all(o.feasible for o in legs)
    drive_time_s = sum(o.travel_time_s for o in legs) if feasible else None
    distance_m = sum(o.distance_m for o in legs) if feasible else None

    return ExecutionResult(
        information_model=information_model,
        feasible=feasible,
        drive_time_s=drive_time_s,
        distance_m=distance_m,
        legs=legs,
        first_blockage=first_blockage,
    )


__all__ = [
    "BlockageInfo",
    "ExecutionResult",
    "InformationModel",
    "LegOutcome",
    "execute_solution",
]
