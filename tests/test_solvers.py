"""Tests for dlm.solver — added in Stage 4, extended in Stage 8.

Offline tests use the tiny fixture graph, with a known optimum derived by
hand (see the module docstring in tests/test_matrix.py for the same
graph's hand-derived costs — this file's known optimum is built directly
on those numbers). Real-network tests exercise the full pipeline
(graph -> matrix -> solve -> T1) against the real cached Dublin graph and
the three committed canonical instances.
"""

from __future__ import annotations

import pytest

from dlm.instance.matrix import DEFAULT_WEIGHT, _build
from dlm.instance.schema import Depot, Instance, Stop, StopSource
from dlm.network.travel_time import add_travel_times
from dlm.simulation.metrics import compute_t1
from dlm.solver.base import route_time_s
from dlm.solver.nearest_neighbour import NearestNeighbourSolver, _construct_order
from dlm.solver.two_opt import TwoOptSolver, two_opt_improve
from tests.fixtures.tiny_graph import make_tiny_graph

# ---------------------------------------------------------------------------
# Offline: tiny fixture, known optimum
# ---------------------------------------------------------------------------

# From tests/test_matrix.py's hand-derived costs on nodes {1,2,3,4}
# (depot=1, stops at nodes 2,3,4): every one of the 6 possible visit orders'
# total cost, computed by hand from the edge times documented there:
#   1->2->3->4->1 = 73.1434  (nearest-neighbour's greedy choice)
#   1->2->4->3->1 = 59.6434
#   1->3->2->4->1 = 73.1434
#   1->3->4->2->1 = 73.1434
#   1->4->2->3->1 = 58.7434
#   1->4->3->2->1 = 45.2434  <- the true optimum, and NN's route reversed


@pytest.fixture
def tiny_instance_and_matrix():
    G = make_tiny_graph()
    G, _ = add_travel_times(G)
    matrix = _build(G, [1, 2, 3, 4], DEFAULT_WEIGHT)

    depot = Depot(
        id="depot", label="depot", lat=53.3400, lon=-6.2700, node=1, source=StopSource.LATLON
    )
    stops = [
        Stop(id="s1", label="node2", lat=53.3410, lon=-6.2690, node=2, source=StopSource.LATLON),
        Stop(id="s2", label="node3", lat=53.3420, lon=-6.2680, node=3, source=StopSource.LATLON),
        Stop(id="s3", label="node4", lat=53.3400, lon=-6.2680, node=4, source=StopSource.LATLON),
    ]
    instance = Instance(name="tiny", depot=depot, stops=stops)
    return instance, matrix


def test_nearest_neighbour_alone_finds_the_worst_tour(tiny_instance_and_matrix) -> None:
    """A deliberately illustrative property of this fixture: NN's greedy
    choice happens to produce the single worst of the 6 possible tours —
    which is exactly what makes it a good test of 2-opt actually working,
    not a coincidence to explain away."""
    instance, matrix = tiny_instance_and_matrix
    order = _construct_order(instance, matrix)
    cost = route_time_s(instance, matrix, order)
    assert order == ["s1", "s2", "s3"]  # depot -> node2 -> node3 -> node4 -> depot
    assert cost == pytest.approx(73.14339059890241)


def test_two_opt_finds_the_known_optimum(tiny_instance_and_matrix) -> None:
    instance, matrix = tiny_instance_and_matrix
    nn_order = _construct_order(instance, matrix)

    improved_order, trajectory = two_opt_improve(instance, matrix, nn_order)
    final_cost = route_time_s(instance, matrix, improved_order)

    assert improved_order == ["s3", "s2", "s1"]  # depot -> node4 -> node3 -> node2 -> depot
    assert final_cost == pytest.approx(45.243390598902415)
    # this genuinely is the best of all 6 possible orders (checked exhaustively):
    import itertools

    all_costs = [
        route_time_s(instance, matrix, list(perm))
        for perm in itertools.permutations(["s1", "s2", "s3"])
    ]
    assert final_cost == pytest.approx(min(all_costs))


def test_two_opt_trajectory_is_monotone_non_increasing(tiny_instance_and_matrix) -> None:
    instance, matrix = tiny_instance_and_matrix
    nn_order = _construct_order(instance, matrix)
    _order, trajectory = two_opt_improve(instance, matrix, nn_order)

    assert trajectory == sorted(trajectory, reverse=True)
    assert all(a >= b for a, b in zip(trajectory[:-1], trajectory[1:], strict=True))


def test_two_opt_solver_end_to_end_matches_known_optimum(tiny_instance_and_matrix) -> None:
    instance, matrix = tiny_instance_and_matrix
    solution = TwoOptSolver().solve(instance, matrix)
    assert solution.order == ["s3", "s2", "s1"]
    assert solution.total_time_s == pytest.approx(45.243390598902415)
    assert len(solution.legs) == 4  # depot->s3, s3->s2, s2->s1, s1->depot


@pytest.mark.parametrize("n_stops", [0, 1, 2])
def test_solvers_handle_degenerate_sizes_without_crashing(
    tiny_instance_and_matrix, n_stops
) -> None:
    instance, matrix = tiny_instance_and_matrix
    small_instance = instance.model_copy(update={"stops": instance.stops[:n_stops]})

    for solver in (NearestNeighbourSolver(), TwoOptSolver()):
        solution = solver.solve(small_instance, matrix)
        assert len(solution.order) == n_stops
        assert len(solution.legs) == n_stops + 1
        assert solution.total_time_s >= 0.0


# ---------------------------------------------------------------------------
# Real-network tests
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
@pytest.mark.parametrize("name,expected_n", [("small", 8), ("medium", 20), ("large", 40)])
def test_solver_runs_correctly_for_n_8_20_40(dublin_graph_and_report, name, expected_n) -> None:
    instance, matrix = _built_instance_and_matrix(name, dublin_graph_and_report)
    assert instance.n_stops == expected_n

    solution = TwoOptSolver().solve(instance, matrix)

    assert len(solution.order) == expected_n
    assert set(solution.order) == {s.id for s in instance.stops}
    assert len(solution.legs) == expected_n + 1
    assert solution.total_time_s > 0.0


@pytest.mark.network
@pytest.mark.parametrize("name", ["small", "medium", "large"])
def test_two_opt_never_increases_cost_on_real_instances(dublin_graph_and_report, name) -> None:
    instance, matrix = _built_instance_and_matrix(name, dublin_graph_and_report)
    nn_order = _construct_order(instance, matrix)
    _order, trajectory = two_opt_improve(instance, matrix, nn_order)
    assert all(a >= b for a, b in zip(trajectory[:-1], trajectory[1:], strict=True))


@pytest.mark.network
def test_t1_reports_breakdown_matching_solution_totals(dublin_graph_and_report) -> None:
    instance, matrix = _built_instance_and_matrix("small", dublin_graph_and_report)
    solution = TwoOptSolver().solve(instance, matrix)
    t1 = compute_t1(instance, solution)

    assert t1.drive_time_s == pytest.approx(solution.total_time_s)
    assert t1.distance_m == pytest.approx(solution.total_distance_m)
    assert t1.total_time_s == pytest.approx(t1.drive_time_s + t1.service_time_s)
    assert t1.n_stops_served == len(instance.stops)
    assert len(t1.legs) == len(solution.legs)
    # every stop's service time defaulted (none has been set explicitly yet)
    from dlm.config import settings

    assert t1.service_time_s == pytest.approx(len(instance.stops) * settings.default_service_time_s)


@pytest.mark.network
def test_rerunning_the_same_instance_gives_identical_t1(dublin_graph_and_report) -> None:
    instance, matrix = _built_instance_and_matrix("small", dublin_graph_and_report)

    solution_1 = TwoOptSolver().solve(instance, matrix)
    solution_2 = TwoOptSolver().solve(instance, matrix)

    t1_1 = compute_t1(instance, solution_1)
    t1_2 = compute_t1(instance, solution_2)

    assert solution_1.order == solution_2.order
    assert t1_1.total_time_s == t1_2.total_time_s
    assert t1_1.distance_m == t1_2.distance_m
