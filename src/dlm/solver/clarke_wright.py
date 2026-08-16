"""Clarke-Wright parallel savings algorithm: Stage 8's hand-implemented
method for `instance.fleet_size > 1` (or a capacity-constrained
`fleet_size == 1`), where NN+2-opt (Stage 4) doesn't apply — that solver
only ever produces one route.

**Savings, adapted for a directed (asymmetric) matrix.** The classical
formula `s(i,j) = cost(i,depot) + cost(depot,j) - cost(i,j)` is exactly
the saving from replacing "return to depot, then depart again" with a
single direct hop `i -> j` when *joining* the route that currently ends
at `i` to the route that currently starts at `j`. Every term is used in
its one natural direction (`i -> depot`, `depot -> j`, `i -> j`), so
unlike Stage 4's 2-opt, this formula needs no adaptation for asymmetry —
it was never assuming `cost(x,y) == cost(y,x)` in the first place.

**Construction, then improvement — the same split as Stage 4.** Savings
merging builds initial routes fast (`O(N^2 log N)` for the sort); each
resulting route is then handed to Stage 4's `two_opt_improve` completely
unchanged, since a single vehicle's route is exactly the single-vehicle
TSP that function already solves.
"""

from __future__ import annotations

from dlm.instance.matrix import Matrix
from dlm.instance.schema import Instance
from dlm.solver.base import FleetSolution, build_fleet_solution
from dlm.solver.two_opt import two_opt_improve

DEFAULT_MAX_ITERATIONS_PER_ROUTE = 2000


def _savings(instance: Instance, matrix: Matrix) -> list[tuple[float, str, str]]:
    depot_node = instance.depot.node
    stops = instance.stops
    savings: list[tuple[float, str, str]] = []
    for i in stops:
        for j in stops:
            if i.id == j.id:
                continue
            s = (
                matrix.get_cost(i.node, depot_node)
                + matrix.get_cost(depot_node, j.node)
                - matrix.get_cost(i.node, j.node)
            )
            savings.append((s, i.id, j.id))
    savings.sort(key=lambda t: t[0], reverse=True)
    return savings


def _merge_routes(instance: Instance, matrix: Matrix) -> list[list[str]]:
    """Greedy parallel-savings merge, respecting `vehicle_capacity`.

    Every stop starts on its own trivial round-trip route. Considering
    merges in descending savings order, two routes are joined (route
    ending at `i` + route starting at `j`) only if they're still distinct
    routes, `i`/`j` are still at the joining ends (not interior — a stop
    already merged into the middle of a route can't be a new join point),
    and the merge doesn't exceed `vehicle_capacity` (if set). Returns
    every resulting route regardless of count — capping to
    `instance.fleet_size` is `ClarkeWrightSolver`'s job, not this
    function's, so it stays testable on its own.
    """
    stops_by_id = {s.id: s for s in instance.stops}
    routes: dict[str, list[str]] = {s.id: [s.id] for s in instance.stops}

    def demand_of(route: list[str]) -> float:
        return sum(stops_by_id[sid].demand for sid in route)

    for _saving, i_id, j_id in _savings(instance, matrix):
        route_i = routes[i_id]
        route_j = routes[j_id]
        if route_i is route_j:
            continue
        if route_i[-1] != i_id or route_j[0] != j_id:
            continue
        if instance.vehicle_capacity is not None:
            if demand_of(route_i) + demand_of(route_j) > instance.vehicle_capacity:
                continue
        merged = route_i + route_j
        for sid in merged:
            routes[sid] = merged

    seen: set[int] = set()
    distinct: list[list[str]] = []
    for route in routes.values():
        if id(route) not in seen:
            seen.add(id(route))
            distinct.append(route)
    return distinct


class ClarkeWrightSolver:
    """`fleet_size`-vehicle CVRP construction (savings) + per-route
    improvement (2-opt)."""

    def __init__(self, max_iterations_per_route: int = DEFAULT_MAX_ITERATIONS_PER_ROUTE) -> None:
        self.max_iterations_per_route = max_iterations_per_route

    def solve_fleet(self, instance: Instance, matrix: Matrix) -> FleetSolution:
        """Build up to `instance.fleet_size` routes.

        If capacity-respecting merging still leaves more routes than
        `instance.fleet_size`, the largest (most-stops) routes are kept —
        this maximises the number of stops served given a fixed vehicle
        budget, since coverage is exactly the sum of kept routes' sizes
        once no further merging is possible. The rest's stops are
        reported in `FleetSolution.unassigned`, not silently dropped.
        """
        routes = _merge_routes(instance, matrix)
        routes.sort(key=len, reverse=True)
        kept = routes[: instance.fleet_size]
        dropped = routes[instance.fleet_size :]
        unassigned = [sid for route in dropped for sid in route]

        improved_routes = []
        trajectory_lengths = []
        for route in kept:
            improved_order, trajectory = two_opt_improve(
                instance, matrix, route, self.max_iterations_per_route
            )
            improved_routes.append(improved_order)
            trajectory_lengths.append(len(trajectory) - 1)

        return build_fleet_solution(
            instance,
            matrix,
            improved_routes,
            unassigned,
            meta={
                "solver": "clarke_wright_2opt",
                "n_routes_before_cap": len(routes),
                "n_vehicles_used": len(improved_routes),
                "two_opt_accepted_per_route": trajectory_lengths,
            },
        )


__all__ = ["ClarkeWrightSolver"]
