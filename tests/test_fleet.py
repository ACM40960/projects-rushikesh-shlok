"""Tests for dlm.solver.clarke_wright / dlm.solver.ortools_solver — added
in Stage 8. See docs/stages/stage-08-fleet-benchmark.md.

Offline tests use a small hand-built "chain" graph (depot -- A -- B -- C
-- D, each hop 10s) where every pairwise cost is simply
`10 * (chain distance)`, so the Clarke-Wright savings formula's output is
directly hand-checkable. Real-network tests exercise the canonical
`fleet` instance (fleet_size=3, vehicle_capacity=10, 15 stops, total
demand exactly 30 — a tight but exactly-feasible fit) and OR-Tools
against it and the `small` instance.
"""

from __future__ import annotations

import networkx as nx
import pytest

from dlm.instance.matrix import DEFAULT_WEIGHT, _build
from dlm.instance.schema import Depot, Instance, Stop, StopSource
from dlm.solver.clarke_wright import ClarkeWrightSolver, _merge_routes, _savings
from dlm.solver.two_opt import TwoOptSolver

# ---------------------------------------------------------------------------
# Offline: a hand-built chain graph
# ---------------------------------------------------------------------------
#
#   depot(0) --10--> A(1) --10--> B(2) --10--> C(3) --10--> D(4)
#   (and the reverse of every edge, same cost)
#
# Every pairwise cost is exactly 10 * (number of hops along the chain):
#   cost(depot,A)=10  cost(depot,B)=20  cost(depot,C)=30  cost(depot,D)=40
#   cost(A,B)=10  cost(A,C)=20  cost(A,D)=30  cost(B,C)=10  cost(B,D)=20
#   cost(C,D)=10
# Savings s(i,j) = cost(i,depot) + cost(depot,j) - cost(i,j):
#   s(A,B)=20  s(A,C)=20  s(A,D)=20  s(B,C)=40  s(B,D)=40  s(C,D)=60
# (and the same values for the reversed pair, since this graph is
# symmetric — asymmetric-cost correctness is already Stage 4/6's job to
# test, not this one's).


def _chain_graph() -> nx.MultiDiGraph:
    G = nx.MultiDiGraph(crs="epsg:4326")
    coords = {
        0: (53.30, -6.30),
        1: (53.31, -6.29),
        2: (53.32, -6.28),
        3: (53.33, -6.27),
        4: (53.34, -6.26),
    }
    for n, (y, x) in coords.items():
        G.add_node(n, y=y, x=x)
    for u, v in [(0, 1), (1, 2), (2, 3), (3, 4)]:
        G.add_edge(u, v, travel_time=10.0, length=10.0)
        G.add_edge(v, u, travel_time=10.0, length=10.0)
    return G


def _chain_instance(
    demand: float = 0.0, vehicle_capacity: float | None = None, fleet_size: int = 1
):
    G = _chain_graph()
    matrix = _build(G, [0, 1, 2, 3, 4], DEFAULT_WEIGHT)
    depot = Depot(id="depot", label="depot", lat=53.30, lon=-6.30, node=0, source=StopSource.LATLON)
    stops = [
        Stop(
            id="A", label="A", lat=53.31, lon=-6.29, node=1, source=StopSource.LATLON, demand=demand
        ),
        Stop(
            id="B", label="B", lat=53.32, lon=-6.28, node=2, source=StopSource.LATLON, demand=demand
        ),
        Stop(
            id="C", label="C", lat=53.33, lon=-6.27, node=3, source=StopSource.LATLON, demand=demand
        ),
        Stop(
            id="D", label="D", lat=53.34, lon=-6.26, node=4, source=StopSource.LATLON, demand=demand
        ),
    ]
    instance = Instance(
        name="chain",
        depot=depot,
        stops=stops,
        fleet_size=fleet_size,
        vehicle_capacity=vehicle_capacity,
    )
    return instance, matrix


def test_savings_formula_matches_hand_calculation() -> None:
    instance, matrix = _chain_instance()
    savings = {(i, j): s for s, i, j in _savings(instance, matrix)}
    assert savings[("A", "B")] == pytest.approx(20.0)
    assert savings[("A", "C")] == pytest.approx(20.0)
    assert savings[("A", "D")] == pytest.approx(20.0)
    assert savings[("B", "C")] == pytest.approx(40.0)
    assert savings[("B", "D")] == pytest.approx(40.0)
    assert savings[("C", "D")] == pytest.approx(60.0)


def test_unconstrained_merging_produces_a_single_route() -> None:
    instance, matrix = _chain_instance()
    routes = _merge_routes(instance, matrix)
    assert len(routes) == 1
    assert set(routes[0]) == {"A", "B", "C", "D"}


def test_two_opt_finds_the_obviously_optimal_chain_order() -> None:
    """The chain topology means depot-A-B-C-D-depot (walk the chain, then
    one long hop back) is cheaper than any order that back-tracks."""
    instance, matrix = _chain_instance(fleet_size=1)
    fleet = ClarkeWrightSolver().solve_fleet(instance, matrix)
    assert len(fleet.routes) == 1
    assert fleet.routes[0].order == ["A", "B", "C", "D"]
    # depot->A->B->C->D->depot: three 10s hops along the chain, then one
    # 10s hop into D, then the 40s hop straight back to depot.
    assert fleet.routes[0].total_time_s == pytest.approx(10 + 10 + 10 + 10 + 40)
    assert fleet.unassigned == []


def test_capacity_and_fleet_size_together_produce_honest_unassigned() -> None:
    """demand=1 each, capacity=2, fleet_size=1: at most 2 stops can ever
    be served (one vehicle, capacity 2) — the other 2 must be reported as
    unassigned, not silently dropped or forced over capacity."""
    instance, matrix = _chain_instance(demand=1.0, vehicle_capacity=2.0, fleet_size=1)
    fleet = ClarkeWrightSolver().solve_fleet(instance, matrix)
    assert len(fleet.routes) == 1
    assert len(fleet.routes[0].order) == 2
    assert len(fleet.unassigned) == 2
    assert set(fleet.routes[0].order) | set(fleet.unassigned) == {"A", "B", "C", "D"}
    total_demand_served = len(fleet.routes[0].order) * 1.0
    assert total_demand_served <= instance.vehicle_capacity


def test_capacity_respected_with_more_vehicles_available() -> None:
    """Same capacity=2 per vehicle, but fleet_size=2: now all 4 stops fit
    (2 vehicles x capacity 2), nothing unassigned."""
    instance, matrix = _chain_instance(demand=1.0, vehicle_capacity=2.0, fleet_size=2)
    fleet = ClarkeWrightSolver().solve_fleet(instance, matrix)
    assert fleet.unassigned == []
    assert len(fleet.routes) == 2
    for route in fleet.routes:
        assert len(route.order) <= 2


def test_solver_records_two_opt_activity_in_meta() -> None:
    instance, matrix = _chain_instance(fleet_size=1)
    fleet = ClarkeWrightSolver().solve_fleet(instance, matrix)
    assert fleet.meta["solver"] == "clarke_wright_2opt"
    assert "two_opt_accepted_per_route" in fleet.meta
    assert len(fleet.meta["two_opt_accepted_per_route"]) == len(fleet.routes)


def test_fleet_solution_totals_match_sum_of_routes() -> None:
    instance, matrix = _chain_instance(demand=1.0, vehicle_capacity=2.0, fleet_size=2)
    fleet = ClarkeWrightSolver().solve_fleet(instance, matrix)
    assert fleet.total_time_s == pytest.approx(sum(r.total_time_s for r in fleet.routes))
    assert fleet.total_distance_m == pytest.approx(sum(r.total_distance_m for r in fleet.routes))


# ---------------------------------------------------------------------------
# Real-network: the canonical `fleet` instance + OR-Tools
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def dublin_graph_and_report():
    from dlm.network.loader import build_graph

    return build_graph()


def _built_instance_and_matrix(name: str, dublin_graph_and_report):
    from pathlib import Path

    from dlm.instance.builder import InstanceBuilder
    from dlm.instance.matrix import build_matrix

    graph, report = dublin_graph_and_report
    builder = InstanceBuilder.load(graph, Path(f"data/instances/{name}.json"))
    instance = builder.build()
    nodes = [instance.depot.node, *(s.node for s in instance.stops)]
    matrix, _ = build_matrix(graph, nodes, graph_id=report.cache_path.stem)
    return instance, matrix


@pytest.mark.network
def test_clarke_wright_serves_every_stop_on_the_canonical_fleet_instance(
    dublin_graph_and_report,
) -> None:
    instance, matrix = _built_instance_and_matrix("fleet", dublin_graph_and_report)
    assert sum(s.demand for s in instance.stops) == pytest.approx(
        instance.vehicle_capacity * instance.fleet_size
    )

    fleet = ClarkeWrightSolver().solve_fleet(instance, matrix)

    assert fleet.unassigned == []
    assert len(fleet.routes) == instance.fleet_size
    served = {sid for r in fleet.routes for sid in r.order}
    assert served == {s.id for s in instance.stops}
    for route in fleet.routes:
        demand = sum(next(s.demand for s in instance.stops if s.id == sid) for sid in route.order)
        assert demand <= instance.vehicle_capacity


@pytest.mark.network
def test_ortools_single_route_is_a_sane_benchmark_for_two_opt(dublin_graph_and_report) -> None:
    instance, matrix = _built_instance_and_matrix("small", dublin_graph_and_report)
    from dlm.solver.ortools_solver import OrToolsSolver

    nn2opt = TwoOptSolver().solve(instance, matrix)
    or_solution = OrToolsSolver(time_limit_s=8).solve(instance, matrix)

    assert set(or_solution.order) == set(nn2opt.order)
    # a real metaheuristic given a real time budget should never land far
    # worse than the simple heuristic it's benchmarking — a loose sanity
    # bound, not a claim that OR-Tools always wins outright.
    assert or_solution.total_time_s <= nn2opt.total_time_s * 1.1


@pytest.mark.network
def test_ortools_fleet_respects_the_same_capacity_as_clarke_wright(
    dublin_graph_and_report,
) -> None:
    from dlm.solver.ortools_solver import OrToolsSolver

    instance, matrix = _built_instance_and_matrix("fleet", dublin_graph_and_report)
    fleet = OrToolsSolver(time_limit_s=8).solve_fleet(instance, matrix, apply_time_windows=False)

    served = {sid for r in fleet.routes for sid in r.order}
    assert served | set(fleet.unassigned) == {s.id for s in instance.stops}
    for route in fleet.routes:
        demand = sum(next(s.demand for s in instance.stops if s.id == sid) for sid in route.order)
        assert demand <= instance.vehicle_capacity + 1e-6


@pytest.mark.network
def test_ortools_time_window_drops_an_unreachable_stop_not_the_whole_solve(
    dublin_graph_and_report,
) -> None:
    """A stop with a time window no route can possibly satisfy is
    reported as unassigned (Stage 8's VRPTW demonstration) — the rest of
    the instance still solves, rather than the whole search failing."""
    from dlm.solver.ortools_solver import OrToolsSolver

    instance, matrix = _built_instance_and_matrix("small", dublin_graph_and_report)
    stops = list(instance.stops)
    stops[0] = stops[0].model_copy(update={"time_window": (0.0, 1.0)})  # impossible: 1 second
    instance_tw = instance.model_copy(update={"stops": stops})

    fleet = OrToolsSolver(time_limit_s=8).solve_fleet(instance_tw, matrix, apply_time_windows=True)

    assert fleet.meta["time_windows"] is True
    assert stops[0].id in fleet.unassigned
    served = {sid for r in fleet.routes for sid in r.order}
    assert served == {s.id for s in instance.stops if s.id != stops[0].id}
