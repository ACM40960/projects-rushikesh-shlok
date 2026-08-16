"""Tests for dlm.instance.

Split into offline tests (fast, against the tiny fixture graph or pure
local file reads — presets, validation logic) and real-network tests
(marked ``@pytest.mark.network``, against the real cached Dublin graph and
live geocoding) — see tests/test_network.py for the same split rationale.
"""

from __future__ import annotations

import pytest

from dlm.instance.builder import InstanceBuilder
from dlm.instance.geocode import AmbiguousGeocodeError, GeocodeError, geocode
from dlm.instance.presets import PresetNotFoundError, get_preset, load_presets
from dlm.instance.schema import InstanceValidationError, Stop, StopSource
from dlm.network.loader import build_graph
from dlm.network.snapping import SnapError
from tests.fixtures.tiny_graph import make_tiny_graph

# ---------------------------------------------------------------------------
# Offline tests: presets file, and validation logic against the tiny fixture
# ---------------------------------------------------------------------------


def test_presets_file_has_at_least_30_entries_with_categories() -> None:
    presets = load_presets()
    assert len(presets) >= 30
    categories = {p.category for p in presets}
    assert {"hospital", "university", "retail", "suburb", "transport_hub", "landmark"} <= categories


def test_get_preset_unknown_name_raises_with_available_list() -> None:
    with pytest.raises(PresetNotFoundError) as exc_info:
        get_preset("Not A Real Place Zzz")
    assert len(exc_info.value.available) >= 30


def test_get_preset_case_insensitive() -> None:
    p1 = get_preset("trinity college dublin")
    p2 = get_preset("Trinity College Dublin")
    assert p1.lat == p2.lat
    assert p1.lon == p2.lon


def test_build_empty_instance_raises_n_and_depot_errors() -> None:
    G = make_tiny_graph()
    b = InstanceBuilder(G, name="empty", seed=1)
    with pytest.raises(InstanceValidationError) as exc_info:
        b.build()
    joined = " ".join(exc_info.value.errors)
    assert "no depot set" in joined
    assert "at least 1" in joined


def test_build_too_many_stops_raises_clear_error() -> None:
    """Exercises the N<=50 bound directly (bypassing 51 real geocode/snap
    calls) by appending dummy stops straight onto the builder's instance —
    this is testing the bound-check logic in build(), not the snapping
    path, which is already covered elsewhere."""
    G = make_tiny_graph()
    b = InstanceBuilder(G, name="toomany", seed=1)
    b.set_depot_from_latlon(53.34001, -6.27001)
    for i in range(51):
        b.instance.stops.append(
            Stop(id=f"x{i}", label=f"x{i}", lat=53.34, lon=-6.27, node=1, source=StopSource.RANDOM)
        )
    with pytest.raises(InstanceValidationError) as exc_info:
        b.build()
    assert any("at most 50" in e for e in exc_info.value.errors)


def test_build_depot_stop_node_collision_is_a_hard_error() -> None:
    G = make_tiny_graph()
    b = InstanceBuilder(G, name="collide", seed=1)
    b.set_depot_from_latlon(53.34001, -6.27001)  # snaps to node 1
    b.instance.stops.append(
        Stop(id="s1", label="same spot", lat=53.34, lon=-6.27, node=1, source=StopSource.LATLON)
    )
    with pytest.raises(InstanceValidationError) as exc_info:
        b.build()
    assert any("share graph node" in e for e in exc_info.value.errors)


def test_build_stop_stop_node_collision_is_only_a_warning(caplog: pytest.LogCaptureFixture) -> None:
    G = make_tiny_graph()
    b = InstanceBuilder(G, name="softcollide", seed=1)
    b.set_depot_from_latlon(53.34098, -6.26899)  # snaps to node 2, away from the stops below
    b.add_stop_from_latlon(53.34001, -6.27001, label="stop A")  # snaps to node 1
    with caplog.at_level("WARNING"):
        b.add_stop_from_latlon(53.34002, -6.27002, label="stop B, same spot as A")  # also node 1
    assert "shares graph node" in caplog.text
    # does NOT raise - two stops sharing a node is a soft problem
    inst = b.build()
    assert inst.n_stops == 2


def test_move_and_rename_unknown_stop_id_raises_keyerror() -> None:
    G = make_tiny_graph()
    b = InstanceBuilder(G, name="badid", seed=1)
    b.set_depot_from_latlon(53.34001, -6.27001)
    with pytest.raises(KeyError):
        b.remove_stop("s99")
    with pytest.raises(KeyError):
        b.rename_stop("s99", "new label")
    with pytest.raises(KeyError):
        b.move_stop("s99", 53.34, -6.27)


# ---------------------------------------------------------------------------
# Real-network tests
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def dublin_graph():
    G, _report = build_graph()
    return G


@pytest.mark.network
@pytest.mark.parametrize("n", [1, 2, 3, 8, 20, 40])
def test_instance_builds_and_round_trips_for_various_n(tmp_path, dublin_graph, n: int) -> None:
    """N=1 and N=2 are the edge cases that break naive solvers/matrices in
    later stages; N=3/8/20/40 sweep the rest of the realistic range. No
    downstream call here assumes a fixed N."""
    b = InstanceBuilder(dublin_graph, name=f"param_{n}", seed=123)
    b.set_depot_from_preset("Connolly Station")
    results = b.add_random_stops(n, seed=123)
    assert len(results) == n

    inst = b.build()
    assert inst.n_stops == n
    assert len(inst.stops) == n

    path = tmp_path / f"param_{n}.json"
    b.save(path)
    b2 = InstanceBuilder.load(dublin_graph, path)
    assert b2.build().n_stops == n


@pytest.mark.network
def test_add_remove_add_equals_direct_construction(dublin_graph) -> None:
    """Add X, remove X, add Y must produce the same stop *content* as
    directly adding only Y. Stop ids are NOT compared: ids are a
    monotonically increasing counter reflecting mutation history (so a
    stop keeps its id across later, unrelated mutations), not final list
    position — see the design note in dlm.instance.builder. Comparing
    content (label/coords/node/source) is the correct, intended notion of
    "identical" here.
    """
    b1 = InstanceBuilder(dublin_graph, name="path1", seed=1)
    b1.set_depot_from_preset("Connolly Station")
    b1.add_stop_from_preset("Grafton Street")
    b1.remove_stop("s1")
    b1.add_stop_from_preset("Trinity College Dublin")
    inst1 = b1.build()

    b2 = InstanceBuilder(dublin_graph, name="path2", seed=1)
    b2.set_depot_from_preset("Connolly Station")
    b2.add_stop_from_preset("Trinity College Dublin")
    inst2 = b2.build()

    def content(inst):
        return [(s.label, s.lat, s.lon, s.node, s.source) for s in inst.stops]

    assert content(inst1) == content(inst2)
    assert inst1.depot.node == inst2.depot.node
    assert inst1.n_stops == inst2.n_stops == 1


@pytest.mark.network
def test_round_trip_save_load_is_lossless(tmp_path, dublin_graph) -> None:
    b = InstanceBuilder(dublin_graph, name="rt", seed=99, fleet_size=2, vehicle_capacity=500.0)
    b.set_depot_from_preset("Connolly Station")
    b.add_stop_from_preset("Trinity College Dublin")
    b.add_stop_from_latlon(53.3382, -6.2591, label="Rathmines pt")
    b.rename_stop("s2", "Rathmines Corner")

    path = tmp_path / "rt.json"
    b.save(path)
    b2 = InstanceBuilder.load(dublin_graph, path)

    assert b2.instance == b.instance
    assert b2.instance.model_dump_json() == b.instance.model_dump_json()


@pytest.mark.network
def test_same_place_via_preset_latlon_and_address_resolves_same_node(dublin_graph) -> None:
    """The three input modes are interchangeable: address / lat-lon /
    preset of the *same* real place all resolve to the same graph node.

    Uses "Grafton Street", one of the presets whose curated coordinate is
    exactly its own geocode result (a handful of other presets — Trinity
    College, UCD, Dublin Airport, Phoenix Park — deliberately use a
    refined "nearest real drivable road" point instead of their raw
    geocoded centroid, since the centroid itself is too far from any road
    to snap; see the note at the top of dublin_locations.yaml. Re-geocoding
    those specific names at test time would not reproduce the override.)
    """
    preset = get_preset("Grafton Street")
    b = InstanceBuilder(dublin_graph, name="same_place", seed=1)
    b.set_depot_from_preset("Connolly Station")

    from_preset = b.add_stop_from_preset("Grafton Street")
    from_latlon = b.add_stop_from_latlon(preset.lat, preset.lon)
    from_address = b.add_stop_from_address("Grafton Street, Dublin")

    assert from_preset.stop.node == from_latlon.stop.node == from_address.stop.node


@pytest.mark.network
def test_stop_in_irish_sea_raises_readable_error_not_a_stack_trace(dublin_graph) -> None:
    b = InstanceBuilder(dublin_graph, name="sea", seed=1)
    with pytest.raises(SnapError) as exc_info:
        b.set_depot_from_latlon(53.35, -6.05)
    assert "No routable road within" in str(exc_info.value)


@pytest.mark.network
def test_ambiguous_address_returns_candidates_not_a_guess() -> None:
    with pytest.raises(AmbiguousGeocodeError) as exc_info:
        geocode("Main Street")
    assert len(exc_info.value.candidates) >= 2


@pytest.mark.network
def test_unfindable_address_raises_geocode_error() -> None:
    with pytest.raises(GeocodeError):
        geocode("zzzqqqxyznonexistentplace12345")


@pytest.mark.network
def test_canonical_instances_load_and_validate(dublin_graph) -> None:
    from dlm.config import settings

    expected_n = {"small": 8, "medium": 20, "large": 40}
    for name, n in expected_n.items():
        path = settings.instances_dir / f"{name}.json"
        assert path.exists(), f"canonical instance {name} not committed at {path}"
        builder = InstanceBuilder.load(dublin_graph, path)
        inst = builder.build()
        assert inst.n_stops == n
        assert inst.depot is not None
        assert all(s.source.value != "random" for s in inst.stops), (
            f"canonical instance {name} must be built from named locations, not random points"
        )
