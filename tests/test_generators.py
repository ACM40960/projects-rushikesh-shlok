"""Tests for dlm.disruption.generators — added in Stage 7.

Offline tests use the tiny fixture graph to check determinism and that
every shape/effect combination produces a schema-valid `Scenario` without
touching the network. Real-network tests confirm generated scenarios
always resolve against the real Dublin graph (the whole point of drawing
geometry from the graph's own nodes/edges).
"""

from __future__ import annotations

import pytest

from dlm.disruption.generators import generate_random_scenario, generate_random_scenarios
from dlm.disruption.schema import DisruptionEffect, DisruptionShape
from dlm.network.travel_time import add_travel_times
from tests.fixtures.tiny_graph import make_tiny_graph

# ---------------------------------------------------------------------------
# Offline: tiny fixture graph
# ---------------------------------------------------------------------------


@pytest.fixture
def tiny_dublin_graph():
    G, _ = add_travel_times(make_tiny_graph())
    return G


def test_same_seed_produces_an_identical_scenario(tiny_dublin_graph) -> None:
    sc1 = generate_random_scenario(tiny_dublin_graph, seed=7)
    sc2 = generate_random_scenario(tiny_dublin_graph, seed=7)
    assert sc1.model_dump() == sc2.model_dump()


def test_different_seeds_can_produce_different_scenarios(tiny_dublin_graph) -> None:
    scenarios = [generate_random_scenario(tiny_dublin_graph, seed=s) for s in range(20)]
    dumps = {repr(sc.model_dump()) for sc in scenarios}
    assert len(dumps) > 1  # not every one of 20 seeds collided onto the same scenario


@pytest.mark.parametrize("shape", list(DisruptionShape))
@pytest.mark.parametrize("effect", list(DisruptionEffect))
def test_every_shape_effect_combination_produces_a_valid_scenario(
    tiny_dublin_graph, shape, effect
) -> None:
    sc = generate_random_scenario(tiny_dublin_graph, seed=1, shape=shape, effect=effect)
    d = sc.disruptions[0]
    assert d.shape is shape
    assert d.effect is effect
    if effect is DisruptionEffect.SLOW_ZONE:
        assert d.speed_factor is not None
        assert 0.0 < d.speed_factor < 1.0
    else:
        assert d.speed_factor is None


def test_generate_random_scenarios_uses_sequential_seeds(tiny_dublin_graph) -> None:
    scenarios = generate_random_scenarios(tiny_dublin_graph, n=5, base_seed=100)
    assert len(scenarios) == 5
    expected = [generate_random_scenario(tiny_dublin_graph, seed=100 + i) for i in range(5)]
    assert [sc.model_dump() for sc in scenarios] == [sc.model_dump() for sc in expected]


# ---------------------------------------------------------------------------
# Real-network: generated scenarios must always resolve
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def dublin_graph():
    from dlm.network.loader import build_graph

    graph, _ = build_graph()
    return graph


@pytest.mark.network
@pytest.mark.parametrize("shape", list(DisruptionShape))
def test_generated_scenario_always_resolves_against_the_real_graph(dublin_graph, shape) -> None:
    from dlm.disruption.engine import validate_scenario

    sc = generate_random_scenario(dublin_graph, seed=42, shape=shape)
    report = validate_scenario(dublin_graph, sc)
    assert report.valid, report.errors
    assert all(count > 0 for count in report.resolved_edge_counts.values())


@pytest.mark.network
def test_a_batch_of_generated_scenarios_applies_cleanly(dublin_graph) -> None:
    from dlm.disruption.engine import apply_scenario

    for sc in generate_random_scenarios(dublin_graph, n=15, base_seed=1000):
        result = apply_scenario(dublin_graph, sc)
        assert result.n_edges_closed > 0 or result.n_edges_slowed > 0
