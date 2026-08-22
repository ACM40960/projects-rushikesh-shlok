"""Detect a blockage during execution and trigger re-optimisation of the
remaining stops from the vehicle's current position.

This is ``T3`` — "cost of the re-optimised route after the disruption" —
and it is deliberately anchored to a **reactive** execution
(`dlm.simulation.execution.execute_solution`,
`InformationModel.REACTIVE`): re-optimisation is what a real dispatcher
does the moment a driver reports being blocked, which is a `reactive`
event by definition (nothing to react to if you already knew in advance —
see the `execution` module docstring for why `omniscient` never produces
a `BlockageInfo` to trigger this from).

If the reactive execution never hits a closure at all (a slow zone only,
or a disruption that misses the route entirely), there is nothing to
re-optimise: `replan_from_blockage` is a no-op that returns the same cost
as the reactive execution unchanged (`triggered=False`) — this is exactly
what makes `T1 == T2 == T3` under a no-op disruption a meaningful
regression test, not a special case.
"""

from __future__ import annotations

from dataclasses import dataclass

import networkx as nx

from dlm.instance.matrix import DEFAULT_WEIGHT, _build, _path_distance_m
from dlm.instance.schema import Depot, Instance, StopSource
from dlm.simulation.execution import ExecutionResult
from dlm.solver.base import Solution, Solver
from dlm.solver.two_opt import two_opt_path_improve


@dataclass(frozen=True)
class ReplanResult:
    """The outcome of re-optimising (or not needing to) after a `reactive`
    execution's first blockage.

    Attributes
    ----------
    triggered : bool
        `False` if `execution_result.first_blockage` was `None` — nothing
        was re-optimised, and `drive_time_s`/`distance_m` are simply
        `execution_result`'s own totals.
    feasible : bool
        `False` if the blockage node cannot reach every remaining stop and
        the true depot on the disrupted graph — no re-optimised route
        exists either, consistent with (never better than) `T2`'s own
        feasibility for the same disruption.
    order : list[str]
        Full stop order actually driven: the already-served prefix,
        unchanged, followed by the re-optimised order of the rest (or the
        original remaining order, if `triggered` is `False`).
    """

    triggered: bool
    feasible: bool
    drive_time_s: float | None
    distance_m: float | None
    order: list[str]


def _strongly_connected_together(graph: nx.MultiDiGraph, nodes: list[int]) -> bool:
    """Whether every node in `nodes` lies in the same strongly-connected
    component of `graph` — i.e. every one of them can reach every other.
    Cheaper and more robust than pairwise `nx.has_path` checks (a single
    O(V+E) decomposition instead of O(N^2) searches), and gives a crisp,
    Stage 5-consistent feasibility check (`nx.is_strongly_connected` on
    the whole graph is the same idea restricted to `nodes = all nodes`).
    """
    node_set = set(nodes)
    for component in nx.strongly_connected_components(graph):
        if node_set <= component:
            return True
    return False


def replan_from_blockage(
    instance: Instance,
    solution: Solution,
    disrupted_graph: nx.MultiDiGraph,
    execution_result: ExecutionResult,
    solver: Solver | None = None,
) -> ReplanResult:
    """Re-optimise the not-yet-served stops from wherever
    `execution_result`'s reactive run first discovered a closure.

    The re-optimised sub-problem has the fixed endpoints that the vehicle
    really has: ``blockage -> remaining stops -> real depot``. Its 2-opt
    objective therefore includes the final return to the real depot rather
    than incorrectly closing a tour back at the blockage point.
    """
    blockage = execution_result.first_blockage
    if blockage is None:
        return ReplanResult(
            triggered=False,
            feasible=execution_result.feasible,
            drive_time_s=execution_result.drive_time_s,
            distance_m=execution_result.distance_m,
            order=list(solution.order),
        )

    served_order = solution.order[: blockage.leg_index]
    remaining_order = solution.order[blockage.leg_index :]
    served_time = sum(leg.travel_time_s for leg in execution_result.legs[: blockage.leg_index])
    served_dist = sum(leg.distance_m for leg in execution_result.legs[: blockage.leg_index])
    served_time += blockage.partial_time_s
    served_dist += blockage.partial_distance_m

    stops_by_id = {s.id: s for s in instance.stops}
    remaining_stops = [stops_by_id[sid] for sid in remaining_order]
    required_nodes = [blockage.node, instance.depot.node, *(s.node for s in remaining_stops)]

    if not _strongly_connected_together(disrupted_graph, required_nodes):
        return ReplanResult(
            triggered=True, feasible=False, drive_time_s=None, distance_m=None, order=[]
        )

    if remaining_stops:
        sub_points = list(
            dict.fromkeys([blockage.node, instance.depot.node, *(s.node for s in remaining_stops)])
        )
        sub_matrix = _build(disrupted_graph, sub_points, DEFAULT_WEIGHT)
        sub_depot = Depot(
            id="_replan_start",
            label="current position",
            lat=disrupted_graph.nodes[blockage.node]["y"],
            lon=disrupted_graph.nodes[blockage.node]["x"],
            node=blockage.node,
            source=StopSource.LATLON,
        )
        sub_instance = Instance(
            name=f"{instance.name}-replan", depot=sub_depot, stops=remaining_stops
        )
        if solver is None:
            from dlm.solver.nearest_neighbour import _construct_order

            initial_order = _construct_order(sub_instance, sub_matrix)
        else:
            # A caller-supplied solver still provides the initial ordering;
            # the path-specific 2-opt pass corrects its endpoint objective.
            initial_order = solver.solve(sub_instance, sub_matrix).order

        replanned_order, _ = two_opt_path_improve(
            blockage.node,
            instance.depot.node,
            sub_instance,
            sub_matrix,
            initial_order,
        )
        node_by_stop = {stop.id: stop.node for stop in remaining_stops}
        route_nodes = [
            blockage.node,
            *(node_by_stop[stop_id] for stop_id in replanned_order),
            instance.depot.node,
        ]
        remaining_time = 0.0
        remaining_dist = 0.0
        for u, v in zip(route_nodes[:-1], route_nodes[1:], strict=True):
            remaining_time += sub_matrix.get_cost(u, v)
            remaining_dist += sub_matrix.get_distance(u, v)
        final_order = served_order + replanned_order
    else:
        return_path = nx.shortest_path(
            disrupted_graph, blockage.node, instance.depot.node, weight="travel_time"
        )
        remaining_time = 0.0
        for u, v in zip(return_path[:-1], return_path[1:], strict=True):
            remaining_time += min(
                disrupted_graph[u][v].values(), key=lambda data: data["travel_time"]
            )["travel_time"]
        remaining_dist = _path_distance_m(disrupted_graph, return_path)
        final_order = served_order

    return ReplanResult(
        triggered=True,
        feasible=True,
        drive_time_s=served_time + remaining_time,
        distance_m=served_dist + remaining_dist,
        order=final_order,
    )


__all__ = ["ReplanResult", "replan_from_blockage"]
