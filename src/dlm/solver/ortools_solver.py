"""OR-Tools routing model over the same travel-time matrix, used as a
benchmark solver rather than the primary method (ADR-0001: NN+2-opt /
Clarke-Wright are the fixed hand-implemented solvers; OR-Tools exists to
quantify what they give up in solution quality — `dlm benchmark`).

`OrToolsSolver.solve_fleet` handles the general case (any `fleet_size`,
optional `vehicle_capacity`, optional per-stop `time_window`): OR-Tools'
`RoutingModel` is natively a multi-vehicle CVRPTW solver, so one model
setup serves `fleet_size == 1` and `fleet_size > 1` alike — unlike Stage
4/8's hand-implemented solvers, which needed two separate modules
(`two_opt`, `clarke_wright`) because the hand-rolled construction/
improvement logic for "one route" and "many capacity-constrained routes"
are genuinely different algorithms. `solve()` wraps `solve_fleet` for
drop-in parity with the `Solver` protocol (`fleet_size == 1`, no
time windows) so it can benchmark directly against `TwoOptSolver`.

**Time windows are OR-Tools-only, deliberately.** `Stop.time_window` has
existed in the schema since Stage 2 but neither `two_opt`'s nor
`clarke_wright`'s route improvement tracks schedule feasibility — doing
so correctly (a time-window-respecting 2-opt has to re-check every
downstream stop's arrival time after each candidate move, not just total
cost) is substantially more engineering for a hand-implemented v1 than
this project's scope justifies. OR-Tools already solves VRPTW natively
(one more `AddDimension` call), which is exactly the kind of capability
gap a benchmark solver exists to make visible rather than hide.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

from dlm.instance.matrix import Matrix
from dlm.instance.schema import Instance
from dlm.solver.base import FleetSolution, Solution, build_fleet_solution

DEFAULT_TIME_LIMIT_S = 10.0
_DISJUNCTION_PENALTY = 1_000_000
_DEFAULT_HORIZON_S = 6 * 3600
_MAX_CAPACITY_DECIMAL_PLACES = 6


def _scaled_capacity_values(instance: Instance) -> tuple[list[int], int, int]:
    """Convert decimal demands/capacity to exact-enough OR-Tools integers.

    OR-Tools' capacity dimension is integer-valued. Rounding each demand
    independently (the previous implementation) could turn values such as
    ``0.4`` into zero and silently remove the constraint. A shared power-of-
    ten scale preserves up to six decimal places for every value.
    """
    assert instance.vehicle_capacity is not None
    values = [
        Decimal(str(instance.vehicle_capacity)),
        *(Decimal(str(s.demand)) for s in instance.stops),
    ]
    decimal_places = max(0, *(-value.normalize().as_tuple().exponent for value in values))
    scale = 10 ** min(decimal_places, _MAX_CAPACITY_DECIMAL_PLACES)

    def scaled(value: Decimal) -> int:
        return int((value * scale).to_integral_value(rounding=ROUND_HALF_UP))

    return [0, *(scaled(Decimal(str(s.demand))) for s in instance.stops)], scaled(values[0]), scale


class OrToolsSolutionNotFound(RuntimeError):
    """Raised when OR-Tools' search finds no solution at all within the
    time limit (as opposed to a solution that drops some stops via a
    disjunction penalty, which is reported as `FleetSolution.unassigned`
    instead of raising)."""


class OrToolsSolver:
    """CVRP(TW) benchmark solver: `fleet_size` vehicles, optional
    `vehicle_capacity`, optional per-stop `time_window`.
    """

    def __init__(self, time_limit_s: float = DEFAULT_TIME_LIMIT_S) -> None:
        if time_limit_s <= 0:
            raise ValueError("time_limit_s must be greater than zero")
        self.time_limit_s = time_limit_s

    def solve(self, instance: Instance, matrix: Matrix) -> Solution:
        """Single-route solve, for direct comparison with `TwoOptSolver`.

        Requires `instance.fleet_size == 1`; raises if OR-Tools still
        can't serve every stop with that one vehicle (unexpected unless
        `vehicle_capacity` makes it genuinely infeasible).
        """
        if instance.fleet_size != 1:
            raise ValueError(
                f"OrToolsSolver.solve() expects fleet_size == 1, got {instance.fleet_size} "
                f"— use solve_fleet() for multi-vehicle instances."
            )
        fleet = self.solve_fleet(instance, matrix, apply_time_windows=False)
        if fleet.unassigned:
            raise ValueError(
                f"OrToolsSolver could not serve every stop with one vehicle: {fleet.unassigned}"
            )
        if not fleet.routes:
            return build_fleet_solution(instance, matrix, [[]]).routes[0]
        return fleet.routes[0]

    def solve_fleet(
        self,
        instance: Instance,
        matrix: Matrix,
        apply_time_windows: bool = True,
    ) -> FleetSolution:
        from ortools.constraint_solver import pywrapcp, routing_enums_pb2

        depot = instance.depot
        stops = instance.stops
        nodes = [depot.node, *(s.node for s in stops)]
        n = len(nodes)
        num_vehicles = instance.fleet_size

        if n <= 1:
            return build_fleet_solution(instance, matrix, [], meta={"solver": "ortools"})

        manager = pywrapcp.RoutingIndexManager(n, num_vehicles, 0)
        routing = pywrapcp.RoutingModel(manager)

        def time_callback(from_index: int, to_index: int) -> int:
            from_node = nodes[manager.IndexToNode(from_index)]
            to_node = nodes[manager.IndexToNode(to_index)]
            return int(round(matrix.get_cost(from_node, to_node)))

        transit_callback_index = routing.RegisterTransitCallback(time_callback)
        routing.SetArcCostEvaluatorOfAllVehicles(transit_callback_index)

        if instance.vehicle_capacity is not None:
            demands, scaled_capacity, capacity_scale = _scaled_capacity_values(instance)

            def demand_callback(from_index: int) -> int:
                return demands[manager.IndexToNode(from_index)]

            demand_callback_index = routing.RegisterUnaryTransitCallback(demand_callback)
            routing.AddDimensionWithVehicleCapacity(
                demand_callback_index,
                0,
                [scaled_capacity] * num_vehicles,
                True,
                "Capacity",
            )
        else:
            capacity_scale = None

        has_time_windows = apply_time_windows and any(s.time_window is not None for s in stops)
        if has_time_windows:
            from dlm.config import settings

            service_times = [
                0,
                *(
                    int(round(stop.service_time_s or settings.default_service_time_s))
                    for stop in stops
                ),
            ]

            def schedule_callback(from_index: int, to_index: int) -> int:
                from_node_index = manager.IndexToNode(from_index)
                travel_s = time_callback(from_index, to_index)
                return travel_s + service_times[from_node_index]

            schedule_callback_index = routing.RegisterTransitCallback(schedule_callback)
            routing.AddDimension(
                schedule_callback_index,
                _DEFAULT_HORIZON_S,
                _DEFAULT_HORIZON_S,
                False,
                "Time",
            )
            time_dimension = routing.GetDimensionOrDie("Time")
            for idx, stop in enumerate(stops, start=1):
                if stop.time_window is not None:
                    start, end = stop.time_window
                    index = manager.NodeToIndex(idx)
                    time_dimension.CumulVar(index).SetRange(int(start), int(end))

        for idx in range(1, n):
            routing.AddDisjunction([manager.NodeToIndex(idx)], _DISJUNCTION_PENALTY)

        search_parameters = pywrapcp.DefaultRoutingSearchParameters()
        search_parameters.first_solution_strategy = (
            routing_enums_pb2.FirstSolutionStrategy.PATH_CHEAPEST_ARC
        )
        search_parameters.local_search_metaheuristic = (
            routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH
        )
        search_parameters.time_limit.FromMilliseconds(max(1, int(round(self.time_limit_s * 1000))))

        assignment = routing.SolveWithParameters(search_parameters)
        if assignment is None:
            raise OrToolsSolutionNotFound(
                f"OR-Tools found no solution for instance {instance.name!r} "
                f"(fleet_size={num_vehicles}) within {self.time_limit_s}s."
            )

        routes: list[list[str]] = []
        assigned_ids: set[str] = set()
        for vehicle_id in range(num_vehicles):
            index = routing.Start(vehicle_id)
            order: list[str] = []
            while not routing.IsEnd(index):
                node = manager.IndexToNode(index)
                if node != 0:
                    stop_id = stops[node - 1].id
                    order.append(stop_id)
                    assigned_ids.add(stop_id)
                index = assignment.Value(routing.NextVar(index))
            if order:
                routes.append(order)

        unassigned = [s.id for s in stops if s.id not in assigned_ids]

        return build_fleet_solution(
            instance,
            matrix,
            routes,
            unassigned,
            meta={
                "solver": "ortools",
                "time_windows": has_time_windows,
                "n_vehicles_used": len(routes),
                "time_limit_s": self.time_limit_s,
                "capacity_scale": capacity_scale,
            },
        )


__all__ = ["OrToolsSolutionNotFound", "OrToolsSolver"]
