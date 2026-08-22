"""Tests for dlm.simulation — added in Stage 6, including the
T1 == T2 == T3 no-op-disruption regression test.
See docs/stages/stage-06-experiment.md.

Offline tests use a small hand-built "diamond" graph (not the Stage 1/3/4
tiny_graph.py fixture, which is too sparse — a linear chain with no
alternate routes — to demonstrate a *successful* detour; this one has a
genuine second path between the depot and the one stop, so closures can
be shown forcing a detour rather than only ever producing infeasibility).
Every offline number here is hand-derived and cross-checked, not just
asserted — see the module docstring below each fixture's cost derivation.
Real-network tests exercise the full disruption -> T2 -> T3 pipeline
against the real cached Dublin graph and the curated scenario library.
"""

from __future__ import annotations

import networkx as nx
import pytest

from dlm.instance.matrix import DEFAULT_WEIGHT, Matrix, _build
from dlm.instance.schema import Depot, Instance, Stop, StopSource
from dlm.simulation.execution import InformationModel, execute_solution
from dlm.simulation.metrics import (
    compute_saving,
    compute_t1,
    compute_t2,
    compute_t3,
    compute_t3_oracle,
)
from dlm.simulation.replan import replan_from_blockage
from dlm.solver.two_opt import TwoOptSolver, route_path_time_s, two_opt_path_improve

# ---------------------------------------------------------------------------
# Offline: a small "diamond" graph with a genuine alternate route
# ---------------------------------------------------------------------------
#
#     0 --10--> 1 --10--> 2 --10--> 3
#                \                  ^
#                 12                |
#                  \                12
#                   v               |
#                   4 --------------'
#                    \__3__> 2 (shortcut back from 4 to 2)
#     3 --20--> 0  (return leg)
#
# Normal shortest path 0 -> 3: 0-1-2-3 = 30 (the route the solver picks).
# Alternates: 0-1-4-3 = 10+12+12 = 34; 0-1-2-4-3 = 10+10+3+12 = 35.
# Closing edge 2->3 forces a detour once the original path is committed to:
#   - omniscient (fresh shortest path 0->3 on the disrupted graph): 34,
#     via 0-1-4-3 — never goes near node 2 at all.
#   - reactive (walks 0-1-2 = 20, discovers 2->3 is gone, detours 2-4-3
#     = 3+12 = 15 from node 2): 20 + 15 = 35 — one unit worse than
#     omniscient, exactly the cost of the "wasted" trip into node 2 before
#     discovering the closure.
# Additionally closing edge 2->4 removes reactive's only detour: stuck at
# node 2 with no way to node 3 at all (omniscient still finds 0-1-4-3,
# since it never routes through node 2 to begin with).


def _diamond_graph() -> nx.MultiDiGraph:
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
    for u, v, t in [
        (0, 1, 10),
        (1, 2, 10),
        (2, 3, 10),
        (1, 4, 12),
        (4, 3, 12),
        (2, 4, 3),
        (3, 0, 20),
    ]:
        G.add_edge(u, v, travel_time=float(t), length=float(t))
    return G


@pytest.fixture
def diamond_instance_and_solution():
    G = _diamond_graph()
    matrix = _build(G, [0, 1, 2, 3], DEFAULT_WEIGHT)
    depot = Depot(id="depot", label="depot", lat=53.30, lon=-6.30, node=0, source=StopSource.LATLON)
    stop_a = Stop(id="A", label="A", lat=53.33, lon=-6.27, node=3, source=StopSource.LATLON)
    instance = Instance(name="diamond", depot=depot, stops=[stop_a])
    solution = TwoOptSolver().solve(instance, matrix)
    return G, instance, solution


def test_solver_picks_the_direct_path_under_normal_conditions(
    diamond_instance_and_solution,
) -> None:
    _G, _instance, solution = diamond_instance_and_solution
    assert solution.order == ["A"]
    assert solution.legs[0].path == [0, 1, 2, 3]
    assert solution.legs[0].travel_time_s == pytest.approx(30.0)  # depot -> A
    assert solution.legs[1].travel_time_s == pytest.approx(20.0)  # A -> depot
    assert solution.total_time_s == pytest.approx(50.0)  # full round trip


def test_t1_t2_t3_are_identical_under_a_no_op_disruption(diamond_instance_and_solution) -> None:
    """The promised regression test: nothing disrupted -> T1 == T2 == T3,
    for both information models, and replan never triggers."""
    G, instance, solution = diamond_instance_and_solution
    t1 = compute_t1(instance, solution)
    t2_omni = compute_t2(instance, solution, G, InformationModel.OMNISCIENT)
    t2_reactive = compute_t2(instance, solution, G, InformationModel.REACTIVE)
    t3 = compute_t3(instance, solution, G)

    assert t1.total_time_s == t2_omni.total_time_s == t2_reactive.total_time_s == t3.total_time_s
    assert t1.drive_time_s == t2_omni.drive_time_s == t2_reactive.drive_time_s == t3.drive_time_s
    assert t3.triggered is False
    assert compute_saving(t2_reactive, t3) == pytest.approx(0.0)


def test_omniscient_beats_reactive_when_a_closure_forces_a_detour(
    diamond_instance_and_solution,
) -> None:
    G, instance, solution = diamond_instance_and_solution
    disrupted = G.copy()
    disrupted.remove_edge(2, 3)

    t2_omni = compute_t2(instance, solution, disrupted, InformationModel.OMNISCIENT)
    t2_reactive = compute_t2(instance, solution, disrupted, InformationModel.REACTIVE)

    assert t2_omni.feasible and t2_reactive.feasible
    # each drive_time_s is the full round trip: the disrupted out-leg (34
    # omniscient / 35 reactive, see the module docstring) plus the
    # unaffected 20s return leg.
    assert t2_omni.drive_time_s == pytest.approx(34.0 + 20.0)
    assert t2_reactive.drive_time_s == pytest.approx(35.0 + 20.0)
    assert t2_omni.drive_time_s < t2_reactive.drive_time_s


def test_reactive_execution_records_the_blockage_location(diamond_instance_and_solution) -> None:
    G, instance, solution = diamond_instance_and_solution
    disrupted = G.copy()
    disrupted.remove_edge(2, 3)

    execution = execute_solution(disrupted, solution, InformationModel.REACTIVE)
    assert execution.first_blockage is not None
    assert execution.first_blockage.leg_index == 0
    assert execution.first_blockage.node == 2
    assert execution.first_blockage.partial_time_s == pytest.approx(20.0)


def test_t3_replans_from_the_blockage_and_matches_hand_derived_cost(
    diamond_instance_and_solution,
) -> None:
    G, instance, solution = diamond_instance_and_solution
    disrupted = G.copy()
    disrupted.remove_edge(2, 3)

    t3 = compute_t3(instance, solution, disrupted)
    assert t3.triggered is True
    assert t3.feasible is True
    # same round-trip total as reactive here: only one stop, so there is
    # no reordering for replan to gain anything from.
    assert t3.drive_time_s == pytest.approx(35.0 + 20.0)
    assert t3.order == ["A"]


def test_closing_the_only_detour_makes_reactive_and_replan_infeasible_but_not_omniscient(
    diamond_instance_and_solution,
) -> None:
    """A genuine three-way divergence, hand-verified: closing 2->3 *and*
    2->4 leaves reactive execution stuck at node 2 (its only detour was
    also removed), and replanning from that same stuck node can't fix it
    either — but omniscient, which never routes through node 2 at all
    (it goes 0-1-4-3), is entirely unaffected. This is the concrete case
    the `InformationModel` enum exists to distinguish."""
    G, instance, solution = diamond_instance_and_solution
    disrupted = G.copy()
    disrupted.remove_edge(2, 3)
    disrupted.remove_edge(2, 4)

    t2_omni = compute_t2(instance, solution, disrupted, InformationModel.OMNISCIENT)
    t2_reactive = compute_t2(instance, solution, disrupted, InformationModel.REACTIVE)
    t3 = compute_t3(instance, solution, disrupted)

    assert t2_omni.feasible is True
    assert t2_omni.drive_time_s == pytest.approx(34.0 + 20.0)
    assert t2_reactive.feasible is False
    assert t2_reactive.total_time_s is None
    assert t2_reactive.drive_time_s is None
    assert t2_reactive.distance_m is None
    assert t2_reactive.n_stops_served == 0
    assert t2_reactive.service_time_s == 0
    execution = execute_solution(disrupted, solution, InformationModel.REACTIVE)
    assert len(execution.legs) < len(solution.legs)
    assert execution.legs[-1].feasible is False
    assert t3.feasible is False
    assert compute_saving(t2_reactive, t3) is None


def test_path_two_opt_optimises_return_to_the_real_endpoint() -> None:
    depot = Depot(
        id="depot",
        label="depot",
        lat=53.30,
        lon=-6.30,
        node=0,
        source=StopSource.LATLON,
    )
    stops = [
        Stop(id="A", label="A", lat=53.31, lon=-6.29, node=1, source=StopSource.LATLON),
        Stop(id="B", label="B", lat=53.32, lon=-6.28, node=2, source=StopSource.LATLON),
    ]
    instance = Instance(name="path-objective", depot=depot, stops=stops)
    matrix = Matrix(nodes=[0, 1, 2, 9])
    for u in matrix.nodes:
        for v in matrix.nodes:
            matrix.cost[(u, v)] = 0.0 if u == v else 50.0
    matrix.cost.update(
        {
            (0, 1): 1.0,
            (0, 2): 2.0,
            (1, 2): 1.0,
            (2, 1): 1.0,
            (1, 9): 100.0,
            (2, 9): 1.0,
        }
    )

    improved, trajectory = two_opt_path_improve(0, 9, instance, matrix, ["B", "A"])

    assert improved == ["A", "B"]
    assert trajectory[-1] == pytest.approx(3.0)
    assert route_path_time_s(0, 9, instance, matrix, improved) == pytest.approx(3.0)


def test_replan_is_a_no_op_when_reactive_never_hits_a_closure(
    diamond_instance_and_solution,
) -> None:
    """A slow zone (edge still exists, just costlier) never triggers a
    blockage — T3 should equal reactive T2 exactly, `triggered=False`."""
    G, instance, solution = diamond_instance_and_solution
    disrupted = G.copy()
    disrupted[2][3][0]["travel_time"] = 100.0  # slower, not closed

    t2_reactive = compute_t2(instance, solution, disrupted, InformationModel.REACTIVE)
    t3 = compute_t3(instance, solution, disrupted)

    assert t2_reactive.feasible is True
    assert t3.triggered is False
    assert t3.drive_time_s == pytest.approx(t2_reactive.drive_time_s)


def test_t3_oracle_matches_omniscient_on_a_single_stop_instance(
    diamond_instance_and_solution,
) -> None:
    """`T3_oracle` re-solves from scratch with full knowledge — for this
    single-stop instance that's exactly the omniscient per-leg optimum
    (54), and happens to also be at least as good as the reactive-
    triggered `T3` (55) here. That second comparison is *not* a general
    law once N > 1 (both use the same heuristic solver, so a from-scratch
    solve can land in a worse local optimum than one anchored to the
    original route's already-good order) — see
    `docs/stages/stage-06-experiment.md` for a real measured case where
    `T3_oracle > T3`; this test only claims what's true for N=1, where
    2-opt has no reordering ambiguity to get wrong."""
    G, instance, solution = diamond_instance_and_solution
    disrupted = G.copy()
    disrupted.remove_edge(2, 3)

    t2_omni = compute_t2(instance, solution, disrupted, InformationModel.OMNISCIENT)
    t3 = compute_t3(instance, solution, disrupted)
    t3_oracle = compute_t3_oracle(instance, disrupted)

    assert t3_oracle.feasible is True
    assert t3_oracle.drive_time_s == pytest.approx(t2_omni.drive_time_s)
    assert t3_oracle.drive_time_s <= t3.drive_time_s


def test_t3_oracle_infeasible_when_instance_points_are_not_mutually_reachable(
    diamond_instance_and_solution,
) -> None:
    G, instance, _solution = diamond_instance_and_solution
    disrupted = G.copy()
    disrupted.remove_edge(2, 3)
    disrupted.remove_edge(4, 3)  # now nothing at all can reach node 3

    t3_oracle = compute_t3_oracle(instance, disrupted)
    assert t3_oracle.feasible is False
    assert t3_oracle.total_time_s is None


def test_replan_from_blockage_matches_compute_t3_directly(diamond_instance_and_solution) -> None:
    """`dlm.simulation.replan.replan_from_blockage` is the mechanism
    `compute_t3` wraps — check it's usable standalone too."""
    G, instance, solution = diamond_instance_and_solution
    disrupted = G.copy()
    disrupted.remove_edge(2, 3)

    execution = execute_solution(disrupted, solution, InformationModel.REACTIVE)
    replan = replan_from_blockage(instance, solution, disrupted, execution)
    t3 = compute_t3(instance, solution, disrupted)

    assert replan.drive_time_s == pytest.approx(t3.drive_time_s)
    assert replan.triggered == t3.triggered


# ---------------------------------------------------------------------------
# Real-network: curated scenarios against a canonical instance
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def dublin_small_instance_and_solution():
    from pathlib import Path

    from dlm.instance.builder import InstanceBuilder
    from dlm.instance.matrix import build_matrix
    from dlm.network.loader import build_graph

    graph, report = build_graph()
    instance = InstanceBuilder.load(graph, Path("data/instances/small.json")).build()
    nodes = [instance.depot.node, *(s.node for s in instance.stops)]
    matrix, _ = build_matrix(graph, nodes, graph_id=report.cache_path.stem)
    solution = TwoOptSolver().solve(instance, matrix)
    return graph, instance, solution


@pytest.mark.network
def test_t1_t2_t3_identical_on_real_graph_with_no_disruption(
    dublin_small_instance_and_solution,
) -> None:
    graph, instance, solution = dublin_small_instance_and_solution
    t1 = compute_t1(instance, solution)
    t2 = compute_t2(instance, solution, graph, InformationModel.REACTIVE)
    t3 = compute_t3(instance, solution, graph)

    assert t1.total_time_s == pytest.approx(t2.total_time_s)
    assert t1.total_time_s == pytest.approx(t3.total_time_s)
    assert t3.triggered is False


@pytest.mark.network
def test_a_scenario_that_misses_the_route_gives_zero_saving(
    dublin_small_instance_and_solution,
) -> None:
    from dlm.disruption.engine import apply_scenario
    from dlm.disruption.schema import find_scenario, load_scenario

    graph, instance, solution = dublin_small_instance_and_solution
    sc = load_scenario(find_scenario("oconnell_street_protest"))
    result = apply_scenario(graph, sc)

    t2 = compute_t2(instance, solution, result.graph, InformationModel.REACTIVE)
    t3 = compute_t3(instance, solution, result.graph)

    assert t2.feasible and t3.feasible
    assert compute_saving(t2, t3) == pytest.approx(0.0)


@pytest.mark.network
def test_quays_closure_strands_reactive_but_not_omniscient_on_small_instance(
    dublin_small_instance_and_solution,
) -> None:
    """A real finding (not manufactured): the `liffey_quays_closure`
    scenario blocks the `small` instance's depot->s5->s1 leg such that a
    blind (reactive) driver has no detour at all from where they discover
    it, while a driver told about the closure in advance (omniscient)
    still finds a feasible route for the same leg."""
    from dlm.disruption.engine import apply_scenario
    from dlm.disruption.schema import find_scenario, load_scenario

    graph, instance, solution = dublin_small_instance_and_solution
    sc = load_scenario(find_scenario("liffey_quays_closure"))
    result = apply_scenario(graph, sc)

    t2_omni = compute_t2(instance, solution, result.graph, InformationModel.OMNISCIENT)
    t2_reactive = compute_t2(instance, solution, result.graph, InformationModel.REACTIVE)

    assert t2_omni.feasible is True
    assert t2_reactive.feasible is False


@pytest.mark.network
@pytest.mark.parametrize(
    "scenario_name",
    [
        "st_patricks_day_parade",
        "oconnell_street_protest",
        "luas_works_dawson_street",
        "liffey_quays_closure",
    ],
)
def test_compare_pipeline_runs_end_to_end_for_every_curated_scenario(
    dublin_small_instance_and_solution, scenario_name
) -> None:
    """Every curated scenario, applied to a real instance, produces a
    well-formed T2/T3/Saving% - either a real number, or a consistent
    (feasible=False, total_time_s=None) infeasible report. Never a crash,
    never a nonsensical partial result."""
    from dlm.disruption.engine import apply_scenario
    from dlm.disruption.schema import find_scenario, load_scenario

    graph, instance, solution = dublin_small_instance_and_solution
    sc = load_scenario(find_scenario(scenario_name))
    result = apply_scenario(graph, sc)

    t2 = compute_t2(instance, solution, result.graph, InformationModel.REACTIVE)
    t3 = compute_t3(instance, solution, result.graph)
    saving = compute_saving(t2, t3)

    if t2.feasible:
        assert t2.total_time_s is not None and t2.total_time_s > 0
    else:
        assert t2.total_time_s is None
    if t3.feasible:
        assert t3.total_time_s is not None and t3.total_time_s > 0
    else:
        assert t3.total_time_s is None
    if not (t2.feasible and t3.feasible):
        assert saving is None
