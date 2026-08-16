"""Tests for dlm.instance.matrix.

Offline tests use the tiny fixture graph with costs verified independently
by hand (see the docstring on EXPECTED_COSTS below for the derivation, not
just "call networkx and compare" — that would only test the wrapping code,
not the underlying shortest-path logic). Real-network tests (marked
``@pytest.mark.network``) cover the N=20 timing/caching acceptance
criteria against the real cached Dublin graph.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from dlm.instance.matrix import DEFAULT_WEIGHT, Matrix, _build, build_matrix
from dlm.network.travel_time import add_travel_times
from tests.fixtures.tiny_graph import make_tiny_graph

# ---------------------------------------------------------------------------
# Offline: tiny fixture graph, hand-derived expected shortest paths
# ---------------------------------------------------------------------------

# Edge travel times (seconds), computed directly from the fixture's
# length/speed data (see tests/fixtures/tiny_graph.py):
#   1->2: 100 / (50 km/h)        = 7.2
#   2->1: 100 / (50 km/h)        = 7.2
#   2->3: 150 / (80 km/h, imputed "primary") = 6.75
#   3->2: 150 / (80 km/h, imputed "primary") = 6.75
#   1->4: 120 / (20 km/h, imputed "service") = 21.6
#   4->3: 130 / (30 mph = 48.28032 km/h)     = 9.693127...
# Node 5 is excluded (it's a dead end with no outgoing edge, dropped by
# largest-SCC extraction in Stage 1 — never a real instance point).
#
# Shortest paths derived by inspection (the graph is small enough to trace
# by hand): the only routes between {1,2,3,4} go via the 1<->2<->3 loop
# and the one-way spur 1->4->3.
_T12 = 7.2
_T23 = 6.75
_T14 = 21.6
_T43 = 130 / (30 * 1.609344 * 1000 / 3600)

EXPECTED_COSTS = {
    (1, 1): 0.0,
    (1, 2): _T12,
    (1, 3): _T12 + _T23,
    (1, 4): _T14,
    (2, 1): _T12,
    (2, 2): 0.0,
    (2, 3): _T23,
    (2, 4): _T12 + _T14,  # 2->1->4
    (3, 1): _T23 + _T12,  # 3->2->1
    (3, 2): _T23,
    (3, 3): 0.0,
    (3, 4): _T23 + _T12 + _T14,  # 3->2->1->4
    (4, 1): _T43 + _T23 + _T12,  # 4->3->2->1
    (4, 2): _T43 + _T23,  # 4->3->2
    (4, 3): _T43,
    (4, 4): 0.0,
}

EXPECTED_PATHS = {
    (1, 1): [1],
    (1, 2): [1, 2],
    (1, 3): [1, 2, 3],
    (1, 4): [1, 4],
    (2, 1): [2, 1],
    (2, 3): [2, 3],
    (2, 4): [2, 1, 4],
    (3, 1): [3, 2, 1],
    (3, 2): [3, 2],
    (3, 4): [3, 2, 1, 4],
    (4, 1): [4, 3, 2, 1],
    (4, 2): [4, 3, 2],
    (4, 3): [4, 3],
}


@pytest.fixture
def tiny_matrix() -> Matrix:
    G = make_tiny_graph()
    G, _stats = add_travel_times(G)
    return _build(G, [1, 2, 3, 4], DEFAULT_WEIGHT)


def test_matrix_costs_match_hand_derivation(tiny_matrix: Matrix) -> None:
    for (u, v), expected in EXPECTED_COSTS.items():
        assert tiny_matrix.get_cost(u, v) == pytest.approx(expected), (u, v)


def test_matrix_paths_match_hand_derivation(tiny_matrix: Matrix) -> None:
    for (u, v), expected in EXPECTED_PATHS.items():
        assert tiny_matrix.get_path(u, v) == expected, (u, v)


def test_matrix_is_asymmetric_and_rate_matches_hand_count(tiny_matrix: Matrix) -> None:
    # unordered pairs: (1,2) sym, (1,3) sym, (1,4) asym, (2,3) sym, (2,4) asym, (3,4) asym
    stats = tiny_matrix.stats()
    assert stats.asymmetric_pairs == 3
    assert stats.asymmetry_rate == pytest.approx(0.5)


def test_matrix_has_zero_triangle_inequality_violations(tiny_matrix: Matrix) -> None:
    """Shortest-path costs on a single static graph can never violate the
    triangle inequality: d(i,k) is a minimum over all paths i->k, and any
    path i->j->k is one such candidate, so d(i,k) <= d(i,j) + d(j,k)
    always. Zero here is a correctness guarantee, not a coincidence — see
    docs/stages/stage-03-matrix.md."""
    stats = tiny_matrix.stats()
    assert stats.triangle_violations == 0


def test_incremental_add_point_matches_full_rebuild() -> None:
    G = make_tiny_graph()
    G, _ = add_travel_times(G)
    full = _build(G, [1, 2, 3, 4], DEFAULT_WEIGHT)

    incremental = Matrix(nodes=[], weight=DEFAULT_WEIGHT)
    for n in [1, 2, 3]:
        incremental.add_point(G, n)
    incremental.add_point(G, 4)

    assert set(incremental.nodes) == set(full.nodes)
    for u in [1, 2, 3, 4]:
        for v in [1, 2, 3, 4]:
            assert incremental.cost[(u, v)] == full.cost[(u, v)]
            assert incremental.paths[(u, v)] == full.paths[(u, v)]


def test_remove_point_drops_row_and_column_only() -> None:
    G = make_tiny_graph()
    G, _ = add_travel_times(G)
    m = _build(G, [1, 2, 3, 4], DEFAULT_WEIGHT)

    m.remove_point(2)

    assert m.nodes == [1, 3, 4]
    assert (2, 1) not in m.cost
    assert (1, 2) not in m.cost
    assert (2, 3) not in m.paths
    # untouched entries survive unchanged
    assert m.cost[(1, 4)] == pytest.approx(EXPECTED_COSTS[(1, 4)])


def test_move_point_equals_remove_then_add() -> None:
    G = make_tiny_graph()
    G, _ = add_travel_times(G)
    m = _build(G, [1, 2, 3], DEFAULT_WEIGHT)

    m.move_point(G, 2, 4)

    assert m.nodes == [1, 3, 4]
    direct = Matrix(nodes=[], weight=DEFAULT_WEIGHT)
    for n in [1, 3, 4]:
        direct.add_point(G, n)
    for u in [1, 3, 4]:
        for v in [1, 3, 4]:
            assert m.cost[(u, v)] == direct.cost[(u, v)]


def test_save_load_round_trip(tmp_path: Path, tiny_matrix: Matrix) -> None:
    path = tmp_path / "matrix.pkl"
    tiny_matrix.save(path)
    loaded = Matrix.load(path)
    assert loaded.nodes == tiny_matrix.nodes
    assert loaded.cost == tiny_matrix.cost
    assert loaded.paths == tiny_matrix.paths


# ---------------------------------------------------------------------------
# Real-network tests
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def dublin_graph():
    from dlm.network.loader import build_graph

    G, report = build_graph()
    return G, report.cache_path.stem


@pytest.mark.network
def test_n20_matrix_builds_and_caches_instantly(tmp_path, dublin_graph, monkeypatch) -> None:
    from dlm.config import settings
    from dlm.instance.builder import InstanceBuilder

    monkeypatch.setattr(settings, "cache_dir", tmp_path)
    graph, graph_id = dublin_graph
    builder = InstanceBuilder.load(graph, Path("data/instances/medium.json"))
    inst = builder.build()
    nodes = [inst.depot.node, *(s.node for s in inst.stops)]
    assert len(nodes) == 21  # N=20 + depot

    _m1, stats1 = build_matrix(graph, nodes, graph_id, force_rebuild=True)
    assert stats1.from_cache is False
    assert stats1.build_seconds < 60.0  # documented "reasonable time" bound

    _m2, stats2 = build_matrix(graph, nodes, graph_id)
    assert stats2.from_cache is True
    assert stats2.build_seconds < 0.5  # cache-instant


@pytest.mark.network
def test_incremental_add_matches_full_rebuild_on_real_graph(dublin_graph) -> None:
    from dlm.instance.builder import InstanceBuilder

    graph, _graph_id = dublin_graph
    builder = InstanceBuilder.load(graph, Path("data/instances/medium.json"))
    inst = builder.build()
    nodes = [inst.depot.node, *(s.node for s in inst.stops)]

    full = _build(graph, nodes, DEFAULT_WEIGHT)

    incremental = Matrix(nodes=[], weight=DEFAULT_WEIGHT)
    for n in nodes[:-1]:
        incremental.add_point(graph, n)
    incremental.add_point(graph, nodes[-1])

    assert set(incremental.nodes) == set(full.nodes)
    for u in nodes:
        for v in nodes:
            assert incremental.cost[(u, v)] == full.cost[(u, v)]
            assert incremental.paths[(u, v)] == full.paths[(u, v)]


@pytest.mark.network
def test_incremental_add_is_measurably_faster_than_full_rebuild(dublin_graph) -> None:
    import time

    from dlm.instance.builder import InstanceBuilder

    graph, _graph_id = dublin_graph
    builder = InstanceBuilder.load(graph, Path("data/instances/medium.json"))
    inst = builder.build()
    nodes = [inst.depot.node, *(s.node for s in inst.stops)]

    base = _build(graph, nodes[:-1], DEFAULT_WEIGHT)

    t0 = time.time()
    base.add_point(graph, nodes[-1])
    incremental_time = time.time() - t0

    t0 = time.time()
    _build(graph, nodes, DEFAULT_WEIGHT)
    full_time = time.time() - t0

    assert incremental_time < full_time / 3  # a real, not marginal, speedup


@pytest.mark.network
def test_real_graph_matrix_has_nonzero_asymmetry_and_no_triangle_violations(dublin_graph) -> None:
    from dlm.instance.builder import InstanceBuilder

    graph, _graph_id = dublin_graph
    builder = InstanceBuilder.load(graph, Path("data/instances/small.json"))
    inst = builder.build()
    nodes = [inst.depot.node, *(s.node for s in inst.stops)]

    m = _build(graph, nodes, DEFAULT_WEIGHT)
    stats = m.stats()

    assert stats.asymmetric_pairs > 0  # one-way streets, not an undirected graph
    assert stats.triangle_violations == 0
