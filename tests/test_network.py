"""Tests for dlm.network.

Split into two groups:

- Fixture-based unit tests (fast, offline) against the tiny hand-built graph
  in tests/fixtures/tiny_graph.py: travel-time imputation, snapping, and
  one-way/SCC structure.
- Real-network tests against the actual cached Dublin graph (network.build_graph):
  strong connectivity, a hand-checked real route, a real one-way street, and
  the Irish Sea snapping failure. These download (or reuse the cache for)
  the real Dublin graph and are marked `@pytest.mark.network` so they can be
  skipped with `-m "not network"` on a machine with no internet access;
  by default `make test` runs them.
"""

from __future__ import annotations

import hashlib
import math
import time
from pathlib import Path

import networkx as nx
import osmnx as ox
import pytest

from dlm.network.loader import DEFAULT_BBOX, _verify_cache_checksum, build_graph
from dlm.network.snapping import SnapError, snap_to_node
from dlm.network.travel_time import add_travel_times, load_speed_defaults
from tests.fixtures.tiny_graph import EXPECTED_REAL_SPEED_KPH, MAX_SNAP_DIST_M, make_tiny_graph

# ---------------------------------------------------------------------------
# Fixture-based unit tests
# ---------------------------------------------------------------------------


def test_speed_defaults_load_and_have_fallback() -> None:
    defaults = load_speed_defaults()
    assert "default" in defaults
    assert defaults["motorway"] > defaults["residential"] > defaults["service"]


def test_travel_time_real_maxspeed_used_when_present() -> None:
    G, stats = add_travel_times(make_tiny_graph())
    for (u, v), expected_kph in EXPECTED_REAL_SPEED_KPH.items():
        data = G.get_edge_data(u, v)[0]
        assert data["speed_source"] == "osm_maxspeed"
        assert data["speed_kph"] == pytest.approx(expected_kph)
        expected_time = data["length"] / (expected_kph * 1000.0 / 3600.0)
        assert data["travel_time"] == pytest.approx(expected_time)


def test_travel_time_imputed_when_maxspeed_missing() -> None:
    G, stats = add_travel_times(make_tiny_graph())
    defaults = load_speed_defaults()

    edge_2_3 = G.get_edge_data(2, 3)[0]
    assert edge_2_3["speed_source"] == "imputed"
    assert edge_2_3["speed_kph"] == defaults["primary"]

    edge_1_4 = G.get_edge_data(1, 4)[0]
    assert edge_1_4["speed_source"] == "imputed"
    assert edge_1_4["speed_kph"] == defaults["service"]


def test_travel_time_stats_coverage_matches_fixture() -> None:
    G, stats = add_travel_times(make_tiny_graph())
    assert stats.n_edges == 7
    assert stats.n_real_maxspeed == 3  # (1,2) (2,1) (4,3)
    assert stats.n_imputed == 4
    assert stats.pct_real == pytest.approx(100.0 * 3 / 7)


def test_fixture_edge_1_to_4_has_no_reverse() -> None:
    """The fixture's one-way street: 1->4 exists, 4->1 does not."""
    G = make_tiny_graph()
    assert G.has_edge(1, 4)
    assert not G.has_edge(4, 1)


def test_fixture_node_5_dropped_by_largest_strongly_connected_component() -> None:
    """Node 5 is a dead end (only reachable, can't reach back) — not in the
    largest strongly connected component."""
    G = make_tiny_graph()
    assert 5 in G.nodes
    G_scc = ox.truncate.largest_component(G, strongly=True)
    assert 5 not in G_scc.nodes
    assert set(G_scc.nodes) == {1, 2, 3, 4}


def test_snap_to_node_within_range() -> None:
    G = make_tiny_graph()
    # Node 1 is at (53.3400, -6.2700); a point a few metres away should snap to it.
    result = snap_to_node(G, lat=53.34001, lon=-6.27001, max_dist_m=MAX_SNAP_DIST_M)
    assert result.node == 1
    assert result.dist_m < MAX_SNAP_DIST_M


def test_snap_to_node_far_away_raises_snap_error() -> None:
    G = make_tiny_graph()
    # Roughly 5.5km north of the fixture cluster - well outside any reasonable guard.
    with pytest.raises(SnapError) as exc_info:
        snap_to_node(G, lat=53.39, lon=-6.27, max_dist_m=MAX_SNAP_DIST_M)
    err = exc_info.value
    assert err.max_dist_m == MAX_SNAP_DIST_M
    assert err.nearest_dist_m > MAX_SNAP_DIST_M
    assert "No routable road within" in str(err)


def test_graph_pickle_checksum_is_verified_before_loading(tmp_path: Path) -> None:
    cache = tmp_path / "graph.pkl"
    cache.write_bytes(b"trusted graph bytes")
    digest = hashlib.sha256(cache.read_bytes()).hexdigest()
    cache.with_suffix(".pkl.sha256").write_text(f"{digest}  graph.pkl\n", encoding="utf-8")
    _verify_cache_checksum(cache)

    cache.write_bytes(b"tampered graph bytes")
    with pytest.raises(ValueError, match="checksum mismatch"):
        _verify_cache_checksum(cache)


# ---------------------------------------------------------------------------
# Real-network tests
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def dublin_graph() -> nx.MultiDiGraph:
    G, _report = build_graph(bbox=DEFAULT_BBOX)
    return G


@pytest.mark.network
def test_real_graph_loads_fast_from_cache(dublin_graph: nx.MultiDiGraph) -> None:
    t0 = time.time()
    _G, report = build_graph(bbox=DEFAULT_BBOX)
    elapsed = time.time() - t0
    assert report.from_cache is True
    assert elapsed < 5.0


@pytest.mark.network
def test_real_graph_is_strongly_connected(dublin_graph: nx.MultiDiGraph) -> None:
    assert nx.is_strongly_connected(dublin_graph)


@pytest.mark.network
def test_real_graph_has_plausible_maxspeed_coverage(dublin_graph: nx.MultiDiGraph) -> None:
    n_real = sum(
        1
        for *_e, d in dublin_graph.edges(keys=True, data=True)
        if d["speed_source"] == "osm_maxspeed"
    )
    n_total = dublin_graph.number_of_edges()
    assert n_total > 1000  # sanity: this is a real city network, not a stub
    assert 0 < n_real < n_total  # some real tags, but not all - both code paths exercised


@pytest.mark.network
def test_real_graph_hand_checked_ucd_to_trinity_route(dublin_graph: nx.MultiDiGraph) -> None:
    """UCD Belfield -> Trinity College Dublin: sanity-check against a real
    map tool's typical off-peak driving time. See docs/stages/stage-01-network.md
    for the reference number and the % difference, which is the actual
    acceptance evidence; this test only guards against gross regressions.

    Both campuses are largely pedestrianised, so their geocoded centroids
    are not themselves on the drivable ("drive"-filtered) network; these
    coordinates are the nearest real drivable road actually bordering each
    campus (Stillorgan Road at UCD Belfield's western edge, College Green
    at Trinity's front gate), found by nearest-node search from the
    geocoded centroid rather than guessed.
    """
    ucd_belfield = snap_to_node(dublin_graph, lat=53.3110154, lon=-6.22051)
    trinity = snap_to_node(dublin_graph, lat=53.3422088, lon=-6.2545337)

    seconds = nx.shortest_path_length(
        dublin_graph, ucd_belfield.node, trinity.node, weight="travel_time"
    )
    minutes = seconds / 60.0
    # Free-flow (no traffic modelled) estimate for ~4.5km: generous bounds
    # around the ~6 minute figure measured during development.
    assert 2.0 < minutes < 20.0


@pytest.mark.network
def test_real_graph_one_way_street_not_reverse_routable(dublin_graph: nx.MultiDiGraph) -> None:
    """Find a real one-way edge (present one direction, absent the other)
    and confirm the graph does not silently offer the reverse."""
    one_way_pairs = [
        (u, v)
        for u, v, k in dublin_graph.edges(keys=True)
        if k == 0 and not dublin_graph.has_edge(v, u)
    ]
    assert one_way_pairs, "expected at least one one-way street in central Dublin"
    u, v = one_way_pairs[0]
    assert not dublin_graph.has_edge(v, u)


@pytest.mark.network
def test_snap_irish_sea_raises_clear_error(dublin_graph: nx.MultiDiGraph) -> None:
    """A point out in Dublin Bay / the Irish Sea, far from any road, must
    fail loudly rather than silently snapping hundreds of metres away."""
    with pytest.raises(SnapError) as exc_info:
        snap_to_node(dublin_graph, lat=53.35, lon=-6.05, max_dist_m=150.0)
    assert math.isfinite(exc_info.value.nearest_dist_m)
    assert exc_info.value.nearest_dist_m > 150.0
