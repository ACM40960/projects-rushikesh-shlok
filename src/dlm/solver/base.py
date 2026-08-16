"""``Solution`` model and the ``Solver`` protocol every solver implements.

All solvers share this interface so they drop into the Stage 6 experiment
harness unchanged, including the Stage 8 OR-Tools benchmark solver.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from dlm.instance.matrix import Matrix
from dlm.instance.schema import Instance

DEPOT_ID = "depot"


@dataclass(frozen=True)
class Leg:
    """One directed hop in a route, depot included.

    Attributes
    ----------
    from_id, to_id : str
        Stop id, or `DEPOT_ID` ("depot").
    from_node, to_node : int
        Graph node ids matching `from_id`/`to_id`.
    travel_time_s : float
        Shortest-path travel time for this hop, seconds.
    distance_m : float
        Distance travelled along that same shortest-time path, metres.
    path : list[int]
        Full node sequence for this hop (inclusive of both endpoints) —
        what a map draws as this leg's polyline.
    """

    from_id: str
    to_id: str
    from_node: int
    to_node: int
    travel_time_s: float
    distance_m: float
    path: list[int]


@dataclass(frozen=True)
class Solution:
    """A route: a visit order plus its expanded legs and totals.

    Attributes
    ----------
    order : list[str]
        Stop ids in visit order — depot is *not* included here (it is
        always the implicit start and end); see `legs` for the full
        depot-to-depot circuit.
    legs : list[Leg]
        The full circuit, depot -> order[0] -> ... -> order[-1] -> depot.
        `len(legs) == len(order) + 1`.
    total_time_s : float
        Pure driving time: sum of `leg.travel_time_s` over `legs`. Does
        **not** include service time at stops — that is a metrics-layer
        concern (`dlm.simulation.metrics`), not something a solver needs
        to know about.
    total_distance_m : float
        Sum of `leg.distance_m` over `legs`.
    meta : dict[str, Any]
        Solver-specific bookkeeping (e.g. `{"solver": "nn_2opt",
        "two_opt_iterations": 12}`), for logging/debugging — never
        required for correctness.
    """

    order: list[str]
    legs: list[Leg]
    total_time_s: float
    total_distance_m: float
    meta: dict[str, Any] = field(default_factory=dict)


class Solver(Protocol):
    """Every solver takes an instance + its travel-time matrix and returns
    a `Solution`. No solver needs the road graph directly — `Matrix`
    already carries cost, path, and distance for every pair (Stage 3)."""

    def solve(self, instance: Instance, matrix: Matrix) -> Solution: ...


def _node_of(instance: Instance, stop_id: str) -> int:
    if stop_id == DEPOT_ID:
        assert instance.depot is not None
        return instance.depot.node
    for s in instance.stops:
        if s.id == stop_id:
            return s.node
    raise KeyError(f"No stop with id {stop_id!r} in instance {instance.name!r}")


def build_solution(
    instance: Instance,
    matrix: Matrix,
    order: list[str],
    meta: dict[str, Any] | None = None,
) -> Solution:
    """Expand a stop-id visit order into a full `Solution`.

    Shared by every solver: constructs `depot -> order[0] -> ... -> depot`,
    looks up each leg's cost/path/distance from `matrix` (`O(1)` per leg,
    since Stage 3 already computed all-pairs shortest paths), and sums the
    totals. A solver only ever needs to produce the `order` permutation —
    this function is what turns that into something a map or a metrics
    calculation can use.
    """
    full_ids = [DEPOT_ID, *order, DEPOT_ID]
    legs: list[Leg] = []
    for from_id, to_id in zip(full_ids[:-1], full_ids[1:], strict=True):
        from_node = _node_of(instance, from_id)
        to_node = _node_of(instance, to_id)
        legs.append(
            Leg(
                from_id=from_id,
                to_id=to_id,
                from_node=from_node,
                to_node=to_node,
                travel_time_s=matrix.get_cost(from_node, to_node),
                distance_m=matrix.get_distance(from_node, to_node),
                path=matrix.get_path(from_node, to_node),
            )
        )

    return Solution(
        order=list(order),
        legs=legs,
        total_time_s=sum(leg.travel_time_s for leg in legs),
        total_distance_m=sum(leg.distance_m for leg in legs),
        meta=meta or {},
    )


def route_time_s(instance: Instance, matrix: Matrix, order: list[str]) -> float:
    """Total driving time for `depot -> order -> depot`, without
    constructing a full `Solution` — the inner-loop cost function 2-opt
    evaluates many times per improvement pass."""
    full_nodes = [_node_of(instance, stop_id) for stop_id in (DEPOT_ID, *order, DEPOT_ID)]
    return sum(matrix.get_cost(u, v) for u, v in zip(full_nodes[:-1], full_nodes[1:], strict=True))
