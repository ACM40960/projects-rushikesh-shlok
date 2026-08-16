"""Tests for dlm.disruption — added in Stage 5 (see
docs/stages/stage-05-disruptions.md).

Offline tests use the tiny fixture graph (tests/fixtures/tiny_graph.py, the
same one Stages 1/3/4 use) to check shape resolution, effect application,
revert, and the "resolve everything up front, against the pristine graph"
design in isolation. Real-network tests apply the four curated library
scenarios (scenarios/library/) to the real cached Dublin graph.
"""

from __future__ import annotations

import time

import networkx as nx
import pytest
from pydantic import ValidationError

from dlm.disruption.engine import (
    DisruptionResolutionError,
    apply_scenario,
    validate_scenario,
)
from dlm.disruption.schema import (
    Disruption,
    DisruptionEffect,
    DisruptionShape,
    Scenario,
    ScenarioNotFoundError,
    find_scenario,
    list_scenarios,
    load_scenario,
)
from dlm.network.travel_time import add_travel_times
from tests.fixtures.tiny_graph import NODES, make_tiny_graph

# ---------------------------------------------------------------------------
# Offline: tiny fixture graph
# ---------------------------------------------------------------------------


@pytest.fixture
def tiny_dublin_graph() -> nx.MultiDiGraph:
    G, _ = add_travel_times(make_tiny_graph())
    return G


def _wp(node_id: int) -> tuple[float, float]:
    return (NODES[node_id]["y"], NODES[node_id]["x"])


def test_edge_closure_removes_both_directions_by_default(tiny_dublin_graph) -> None:
    sc = Scenario(
        name="t",
        disruptions=[
            Disruption(
                id="d1",
                shape=DisruptionShape.EDGE,
                effect=DisruptionEffect.CLOSURE,
                from_node=1,
                to_node=2,
            )
        ],
    )
    result = apply_scenario(tiny_dublin_graph, sc)
    assert result.affected_edges == {(1, 2, 0), (2, 1, 0)}
    assert not result.graph.has_edge(1, 2)
    assert not result.graph.has_edge(2, 1)
    # base graph untouched
    assert tiny_dublin_graph.has_edge(1, 2)
    assert tiny_dublin_graph.has_edge(2, 1)


def test_edge_closure_directional_forward_only(tiny_dublin_graph) -> None:
    sc = Scenario(
        name="t",
        disruptions=[
            Disruption(
                id="d1",
                shape=DisruptionShape.EDGE,
                effect=DisruptionEffect.CLOSURE,
                from_node=1,
                to_node=2,
                directions="forward",
            )
        ],
    )
    result = apply_scenario(tiny_dublin_graph, sc)
    assert result.affected_edges == {(1, 2, 0)}
    assert result.graph.has_edge(2, 1)  # reverse untouched


def test_node_closure_removes_all_incident_edges(tiny_dublin_graph) -> None:
    sc = Scenario(
        name="t",
        disruptions=[
            Disruption(id="d1", shape=DisruptionShape.NODE, effect=DisruptionEffect.CLOSURE, node=2)
        ],
    )
    result = apply_scenario(tiny_dublin_graph, sc)
    # node 2's incident edges in the fixture: 1->2, 2->1, 2->3, 3->2
    assert result.affected_edges == {(1, 2, 0), (2, 1, 0), (2, 3, 0), (3, 2, 0)}


def test_slow_zone_scales_travel_time_by_inverse_speed_factor(tiny_dublin_graph) -> None:
    original_tt = tiny_dublin_graph[1][2][0]["travel_time"]
    sc = Scenario(
        name="t",
        disruptions=[
            Disruption(
                id="d1",
                shape=DisruptionShape.EDGE,
                effect=DisruptionEffect.SLOW_ZONE,
                from_node=1,
                to_node=2,
                directions="forward",
                speed_factor=0.25,
            )
        ],
    )
    result = apply_scenario(tiny_dublin_graph, sc)
    assert result.graph[1][2][0]["travel_time"] == pytest.approx(original_tt / 0.25)
    (change,) = result.changes
    assert change.kind == "slowed"
    assert change.original_travel_time_s == pytest.approx(original_tt)
    assert change.new_travel_time_s == pytest.approx(original_tt / 0.25)


def test_revert_restores_original_graph_exactly(tiny_dublin_graph) -> None:
    before_edges = sorted(tiny_dublin_graph.edges(keys=True))
    before_attrs = {
        (u, v, k): dict(d) for u, v, k, d in tiny_dublin_graph.edges(keys=True, data=True)
    }

    sc = Scenario(
        name="t",
        disruptions=[
            Disruption(
                id="d1",
                shape=DisruptionShape.EDGE,
                effect=DisruptionEffect.CLOSURE,
                from_node=1,
                to_node=2,
            ),
            Disruption(
                id="d2",
                shape=DisruptionShape.EDGE,
                effect=DisruptionEffect.SLOW_ZONE,
                from_node=3,
                to_node=2,
                directions="forward",
                speed_factor=0.5,
            ),
        ],
    )
    result = apply_scenario(tiny_dublin_graph, sc)
    assert result.changes  # something actually changed
    result.revert()

    assert result.changes == []
    assert sorted(result.graph.edges(keys=True)) == before_edges
    after_attrs = {(u, v, k): dict(d) for u, v, k, d in result.graph.edges(keys=True, data=True)}
    assert after_attrs == before_attrs


def test_resolution_happens_up_front_against_the_pristine_graph(tiny_dublin_graph) -> None:
    """A closure listed *before* a corridor in the same scenario must not
    change what the corridor resolves to: if resolution ran sequentially
    (bug), the corridor's shortest-path search from node 1 to node 3 would
    fail once d1 has already removed edge 1->2 forward. It doesn't, because
    every disruption's shape is resolved against the untouched graph before
    any edge is touched."""
    sc = Scenario(
        name="t",
        disruptions=[
            Disruption(
                id="d1",
                shape=DisruptionShape.EDGE,
                effect=DisruptionEffect.CLOSURE,
                from_node=1,
                to_node=2,
                directions="forward",
            ),
            Disruption(
                id="d2",
                shape=DisruptionShape.CORRIDOR,
                effect=DisruptionEffect.CLOSURE,
                waypoints=[_wp(1), _wp(3)],
            ),
        ],
    )
    result = apply_scenario(tiny_dublin_graph, sc)
    # d2's path (1->2->3) resolves successfully despite d1 already having
    # claimed edge (1,2,0); d1 wins that one edge, d2 picks up the rest.
    assert result.affected_edges == {(1, 2, 0), (2, 1, 0), (2, 3, 0), (3, 2, 0)}
    assert {c.disruption_id for c in result.changes} == {"d1", "d2"}


def test_closure_wins_over_slow_zone_on_the_same_edge(tiny_dublin_graph) -> None:
    sc = Scenario(
        name="t",
        disruptions=[
            Disruption(
                id="slow_first",
                shape=DisruptionShape.EDGE,
                effect=DisruptionEffect.SLOW_ZONE,
                from_node=1,
                to_node=2,
                directions="forward",
                speed_factor=0.5,
            ),
            Disruption(
                id="close_second",
                shape=DisruptionShape.EDGE,
                effect=DisruptionEffect.CLOSURE,
                from_node=1,
                to_node=2,
                directions="forward",
            ),
        ],
    )
    result = apply_scenario(tiny_dublin_graph, sc)
    assert not result.graph.has_edge(1, 2)
    (change,) = result.changes
    assert change.kind == "removed"
    assert change.disruption_id == "close_second"


def test_first_listed_slow_zone_wins_when_two_overlap(tiny_dublin_graph) -> None:
    original_tt = tiny_dublin_graph[1][2][0]["travel_time"]
    sc = Scenario(
        name="t",
        disruptions=[
            Disruption(
                id="first",
                shape=DisruptionShape.EDGE,
                effect=DisruptionEffect.SLOW_ZONE,
                from_node=1,
                to_node=2,
                directions="forward",
                speed_factor=0.5,
            ),
            Disruption(
                id="second",
                shape=DisruptionShape.EDGE,
                effect=DisruptionEffect.SLOW_ZONE,
                from_node=1,
                to_node=2,
                directions="forward",
                speed_factor=0.1,  # would be far slower if compounded/overridden
            ),
        ],
    )
    result = apply_scenario(tiny_dublin_graph, sc)
    assert result.graph[1][2][0]["travel_time"] == pytest.approx(original_tt / 0.5)
    (change,) = result.changes
    assert change.disruption_id == "first"


def test_validate_scenario_reports_unreachable_point(tiny_dublin_graph) -> None:
    sc = Scenario(
        name="t",
        disruptions=[
            Disruption(
                id="d1",
                shape=DisruptionShape.NODE,
                effect=DisruptionEffect.CLOSURE,
                at=(0.0, 0.0),  # nowhere near the fixture graph
            )
        ],
    )
    report = validate_scenario(tiny_dublin_graph, sc)
    assert not report.valid
    assert report.n_disruptions == 1
    assert len(report.errors) == 1
    assert "d1" in report.errors[0]


def test_apply_scenario_raises_resolution_error_before_touching_anything(
    tiny_dublin_graph,
) -> None:
    sc = Scenario(
        name="t",
        disruptions=[
            Disruption(
                id="d1", shape=DisruptionShape.NODE, effect=DisruptionEffect.CLOSURE, at=(0.0, 0.0)
            )
        ],
    )
    before = sorted(tiny_dublin_graph.edges(keys=True))
    with pytest.raises(DisruptionResolutionError):
        apply_scenario(tiny_dublin_graph, sc)
    assert sorted(tiny_dublin_graph.edges(keys=True)) == before


# ---------------------------------------------------------------------------
# Schema validation
# ---------------------------------------------------------------------------


def test_edge_shape_requires_exactly_one_geometry_pair() -> None:
    with pytest.raises(ValidationError):
        Disruption(id="d1", shape=DisruptionShape.EDGE, effect=DisruptionEffect.CLOSURE)
    with pytest.raises(ValidationError):
        Disruption(
            id="d1",
            shape=DisruptionShape.EDGE,
            effect=DisruptionEffect.CLOSURE,
            from_node=1,
            to_node=2,
            from_latlon=(53.0, -6.0),
            to_latlon=(53.1, -6.1),
        )


@pytest.mark.parametrize(
    "kwargs",
    [
        {"shape": DisruptionShape.CORRIDOR, "waypoints": [(53.0, -6.0)]},
        {"shape": DisruptionShape.POLYGON, "boundary": [(53.0, -6.0), (53.1, -6.1)]},
    ],
)
def test_shape_geometry_minimum_point_counts(kwargs) -> None:
    with pytest.raises(ValidationError):
        Disruption(id="d1", effect=DisruptionEffect.CLOSURE, **kwargs)


def test_slow_zone_requires_speed_factor() -> None:
    with pytest.raises(ValidationError):
        Disruption(
            id="d1",
            shape=DisruptionShape.EDGE,
            effect=DisruptionEffect.SLOW_ZONE,
            from_node=1,
            to_node=2,
        )


def test_closure_forbids_speed_factor() -> None:
    with pytest.raises(ValidationError):
        Disruption(
            id="d1",
            shape=DisruptionShape.EDGE,
            effect=DisruptionEffect.CLOSURE,
            from_node=1,
            to_node=2,
            speed_factor=0.5,
        )


def test_time_window_start_must_precede_end() -> None:
    with pytest.raises(ValidationError):
        Disruption(
            id="d1",
            shape=DisruptionShape.EDGE,
            effect=DisruptionEffect.CLOSURE,
            from_node=1,
            to_node=2,
            time_window=(100.0, 50.0),
        )


def test_scenario_rejects_duplicate_disruption_ids() -> None:
    with pytest.raises(ValidationError):
        Scenario(
            name="t",
            disruptions=[
                Disruption(
                    id="dup",
                    shape=DisruptionShape.EDGE,
                    effect=DisruptionEffect.CLOSURE,
                    from_node=1,
                    to_node=2,
                ),
                Disruption(
                    id="dup",
                    shape=DisruptionShape.EDGE,
                    effect=DisruptionEffect.CLOSURE,
                    from_node=2,
                    to_node=3,
                ),
            ],
        )


def test_load_scenario_round_trip(tmp_path) -> None:
    import yaml

    raw = {
        "schema_version": 1,
        "name": "roundtrip",
        "disruptions": [
            {
                "id": "d1",
                "shape": "edge",
                "effect": "closure",
                "from_node": 1,
                "to_node": 2,
            }
        ],
    }
    path = tmp_path / "roundtrip.yaml"
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    sc = load_scenario(path)
    assert sc.name == "roundtrip"
    assert sc.disruptions[0].id == "d1"


def test_find_scenario_missing_raises() -> None:
    with pytest.raises(ScenarioNotFoundError):
        find_scenario("definitely_not_a_real_scenario_name")


# ---------------------------------------------------------------------------
# Real-network: the curated scenario library
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def dublin_graph():
    from dlm.network.loader import build_graph

    graph, _ = build_graph()
    return graph


@pytest.mark.network
def test_curated_library_has_exactly_four_scenarios() -> None:
    library_scenarios = [p for p in list_scenarios() if p.parent.name == "library"]
    assert len(library_scenarios) == 4


@pytest.mark.network
@pytest.mark.parametrize(
    "name",
    [
        "st_patricks_day_parade",
        "oconnell_street_protest",
        "luas_works_dawson_street",
        "liffey_quays_closure",
    ],
)
def test_curated_scenario_validates_against_real_graph(dublin_graph, name) -> None:
    sc = load_scenario(find_scenario(name))
    report = validate_scenario(dublin_graph, sc)
    assert report.valid, report.errors
    assert all(count > 0 for count in report.resolved_edge_counts.values())


@pytest.mark.network
@pytest.mark.parametrize(
    "name,expect_closed,expect_slowed",
    [
        ("st_patricks_day_parade", True, False),
        ("oconnell_street_protest", True, False),
        ("luas_works_dawson_street", False, True),
        ("liffey_quays_closure", True, False),
    ],
)
def test_curated_scenario_applies_without_mutating_base_graph(
    dublin_graph, name, expect_closed, expect_slowed
) -> None:
    before_n_edges = dublin_graph.number_of_edges()
    sc = load_scenario(find_scenario(name))

    result = apply_scenario(dublin_graph, sc)

    assert dublin_graph.number_of_edges() == before_n_edges  # base graph untouched
    assert (result.n_edges_closed > 0) is expect_closed
    assert (result.n_edges_slowed > 0) is expect_slowed


@pytest.mark.network
def test_full_road_closures_can_disconnect_the_real_graph(dublin_graph) -> None:
    """An honest, real finding: closing a real corridor/area in central
    Dublin does remove strong connectivity (alternate routes exist for a
    single bridge, but not for a whole cordoned street), while a slow zone
    never can (it never removes an edge)."""
    assert nx.is_strongly_connected(dublin_graph)

    parade = apply_scenario(dublin_graph, load_scenario(find_scenario("st_patricks_day_parade")))
    assert not nx.is_strongly_connected(parade.graph)

    luas = apply_scenario(dublin_graph, load_scenario(find_scenario("luas_works_dawson_street")))
    assert nx.is_strongly_connected(luas.graph)


@pytest.mark.network
def test_revert_is_fast_and_exact_on_the_real_graph(dublin_graph) -> None:
    before_n_edges = dublin_graph.number_of_edges()
    sc = load_scenario(find_scenario("st_patricks_day_parade"))
    result = apply_scenario(dublin_graph, sc)
    assert result.graph.number_of_edges() == before_n_edges - result.n_edges_closed

    t0 = time.time()
    result.revert()
    revert_s = time.time() - t0

    assert result.graph.number_of_edges() == before_n_edges
    assert revert_s < 0.5  # cheap: touches only the changed edges, not a full re-copy
