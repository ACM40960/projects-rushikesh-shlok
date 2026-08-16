"""Mutable ``InstanceBuilder``: add/remove/move/rename stops.

This is the contract both the CLI (Stage 2) and the Streamlit UI (Stage 10)
call — the UI adds no logic of its own, only widget plumbing around these
methods. ``add_stop_from_latlon``/``set_depot_from_latlon`` take an
explicit ``source`` so Stage 10's map-click handler can reuse them directly
(passing ``StopSource.MAP_CLICK``) rather than needing a separate method.
"""

from __future__ import annotations

import logging
import random
import re
from dataclasses import dataclass
from pathlib import Path

import networkx as nx

from dlm.config import settings
from dlm.instance.geocode import geocode
from dlm.instance.presets import get_preset
from dlm.instance.schema import (
    MAX_STOPS,
    MIN_STOPS,
    Depot,
    Instance,
    InstanceValidationError,
    Stop,
    StopSource,
)
from dlm.network.snapping import DEFAULT_MAX_SNAP_DIST_M, snap_to_node

logger = logging.getLogger(__name__)

_ID_PATTERN = re.compile(r"^s(\d+)$")


@dataclass(frozen=True)
class MutationResult:
    """What a builder mutation did, so callers (CLI and UI alike) can
    report it without re-deriving it.

    Attributes
    ----------
    action : str
        Short machine-readable action name, e.g. ``"add_stop"``.
    message : str
        Human-readable summary.
    stop : Stop, optional
        The stop that was added/moved/renamed, if applicable.
    """

    action: str
    message: str
    stop: Stop | None = None


class InstanceBuilder:
    """Mutable builder around an :class:`~dlm.instance.schema.Instance`.

    Every mutator snaps its point through
    :func:`dlm.network.snapping.snap_to_node` immediately (so a bad point
    fails at the moment it's added, not later at :meth:`build`), and
    returns a :class:`MutationResult` describing what changed.
    """

    def __init__(
        self,
        graph: nx.MultiDiGraph,
        name: str,
        seed: int | None = None,
        fleet_size: int = 1,
        vehicle_capacity: float | None = None,
    ) -> None:
        self.graph = graph
        self.instance = Instance(
            name=name,
            seed=seed if seed is not None else settings.seed,
            fleet_size=fleet_size,
            vehicle_capacity=vehicle_capacity,
        )
        self._id_counter = 1

    # -- id assignment --------------------------------------------------

    def _next_id(self) -> str:
        sid = f"s{self._id_counter}"
        self._id_counter += 1
        return sid

    def _next_id_counter_from_existing(self) -> int:
        max_n = 0
        for s in self.instance.stops:
            m = _ID_PATTERN.match(s.id)
            if m:
                max_n = max(max_n, int(m.group(1)))
        return max_n + 1

    def _find_stop(self, stop_id: str) -> Stop:
        for s in self.instance.stops:
            if s.id == stop_id:
                return s
        raise KeyError(
            f"No stop with id {stop_id!r} in instance {self.instance.name!r} "
            f"(has: {[s.id for s in self.instance.stops]})"
        )

    def _warn_if_node_collision(self, node: int, new_id: str) -> None:
        colliding = [s.id for s in self.instance.stops if s.node == node and s.id != new_id]
        if (
            self.instance.depot is not None
            and self.instance.depot.node == node
            and self.instance.depot.id != new_id
        ):
            colliding.append(self.instance.depot.id)
        if colliding:
            logger.warning(
                "stop %s shares graph node %d with %s — consider merging or moving one",
                new_id,
                node,
                colliding,
            )

    # -- depot ------------------------------------------------------------

    def set_depot_from_latlon(
        self,
        lat: float,
        lon: float,
        label: str | None = None,
        source: StopSource = StopSource.LATLON,
        max_dist_m: float = DEFAULT_MAX_SNAP_DIST_M,
    ) -> MutationResult:
        snap = snap_to_node(self.graph, lat, lon, max_dist_m)
        depot = Depot(
            id="depot",
            label=label or f"Depot ({lat:.5f}, {lon:.5f})",
            lat=lat,
            lon=lon,
            node=snap.node,
            source=source,
        )
        self.instance.depot = depot
        self._warn_if_node_collision(depot.node, "depot")
        return MutationResult(
            "set_depot",
            f"Depot set to {depot.label!r} at node {depot.node} "
            f"({snap.dist_m:.0f}m from query point)",
            depot,
        )

    def set_depot_from_address(self, address: str) -> MutationResult:
        geo = geocode(address)
        result = self.set_depot_from_latlon(
            geo.lat, geo.lon, label=geo.label, source=StopSource.ADDRESS
        )
        return MutationResult(
            "set_depot", f"Depot set from address {address!r}: {result.message}", result.stop
        )

    def set_depot_from_preset(self, name: str) -> MutationResult:
        preset = get_preset(name)
        result = self.set_depot_from_latlon(
            preset.lat, preset.lon, label=preset.name, source=StopSource.PRESET
        )
        return MutationResult(
            "set_depot", f"Depot set from preset {name!r}: {result.message}", result.stop
        )

    # -- stops --------------------------------------------------------------

    def add_stop_from_latlon(
        self,
        lat: float,
        lon: float,
        label: str | None = None,
        source: StopSource = StopSource.LATLON,
        max_dist_m: float = DEFAULT_MAX_SNAP_DIST_M,
    ) -> MutationResult:
        snap = snap_to_node(self.graph, lat, lon, max_dist_m)
        stop = Stop(
            id=self._next_id(),
            label=label or f"({lat:.5f}, {lon:.5f})",
            lat=lat,
            lon=lon,
            node=snap.node,
            source=source,
        )
        self.instance.stops.append(stop)
        self._warn_if_node_collision(stop.node, stop.id)
        return MutationResult(
            "add_stop",
            f"Added stop {stop.id} ({stop.label!r}) at node {stop.node} "
            f"({snap.dist_m:.0f}m from query point)",
            stop,
        )

    def add_stop_from_address(self, address: str, label: str | None = None) -> MutationResult:
        geo = geocode(address)
        result = self.add_stop_from_latlon(
            geo.lat, geo.lon, label=label or geo.label, source=StopSource.ADDRESS
        )
        return MutationResult(
            "add_stop", f"Added stop from address {address!r}: {result.message}", result.stop
        )

    def add_stop_from_preset(self, name: str) -> MutationResult:
        preset = get_preset(name)
        result = self.add_stop_from_latlon(
            preset.lat, preset.lon, label=preset.name, source=StopSource.PRESET
        )
        return MutationResult(
            "add_stop", f"Added stop from preset {name!r}: {result.message}", result.stop
        )

    def add_random_stops(self, n: int, seed: int) -> list[MutationResult]:
        """Add `n` stops sampled from the graph's routable nodes.

        Deterministic given `seed` and the graph (the graph's node
        iteration order is stable for a given cached graph file): the same
        `(graph, seed, n)` always produces the same stops, skipping nodes
        already used by the depot or existing stops.
        """
        rng = random.Random(seed)
        candidate_nodes = list(self.graph.nodes)
        rng.shuffle(candidate_nodes)

        used_nodes = {s.node for s in self.instance.stops}
        if self.instance.depot is not None:
            used_nodes.add(self.instance.depot.node)

        results: list[MutationResult] = []
        for node in candidate_nodes:
            if len(results) >= n:
                break
            if node in used_nodes:
                continue
            used_nodes.add(node)
            data = self.graph.nodes[node]
            lat, lon = float(data["y"]), float(data["x"])
            stop = Stop(
                id=self._next_id(),
                label=f"Random {len(results) + 1}",
                lat=lat,
                lon=lon,
                node=node,
                source=StopSource.RANDOM,
            )
            self.instance.stops.append(stop)
            results.append(
                MutationResult("add_stop", f"Added random stop {stop.id} at node {node}", stop)
            )

        if len(results) < n:
            raise RuntimeError(
                f"Could not find {n} unused routable nodes (only found {len(results)}) "
                "— the graph is too small for this many random stops."
            )
        return results

    def move_stop(
        self, stop_id: str, lat: float, lon: float, max_dist_m: float = DEFAULT_MAX_SNAP_DIST_M
    ) -> MutationResult:
        stop = self._find_stop(stop_id)
        snap = snap_to_node(self.graph, lat, lon, max_dist_m)
        stop.lat, stop.lon, stop.node = lat, lon, snap.node
        self._warn_if_node_collision(stop.node, stop.id)
        return MutationResult(
            "move_stop",
            f"Moved {stop_id} to node {snap.node} ({snap.dist_m:.0f}m from query point)",
            stop,
        )

    def remove_stop(self, stop_id: str) -> MutationResult:
        stop = self._find_stop(stop_id)
        self.instance.stops.remove(stop)
        return MutationResult("remove_stop", f"Removed stop {stop_id} ({stop.label!r})", stop)

    def rename_stop(self, stop_id: str, new_label: str) -> MutationResult:
        stop = self._find_stop(stop_id)
        old_label = stop.label
        stop.label = new_label
        return MutationResult(
            "rename_stop", f"Renamed {stop_id} from {old_label!r} to {new_label!r}", stop
        )

    # -- persistence --------------------------------------------------------

    def save(self, path: Path) -> None:
        """Serialise the current (possibly in-progress) instance to JSON."""
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.instance.model_dump_json(indent=2), encoding="utf-8")

    @classmethod
    def load(cls, graph: nx.MultiDiGraph, path: Path) -> InstanceBuilder:
        """Reconstruct a builder from a previously saved instance JSON."""
        instance = Instance.model_validate_json(path.read_text(encoding="utf-8"))
        builder = cls.__new__(cls)
        builder.graph = graph
        builder.instance = instance
        builder._id_counter = builder._next_id_counter_from_existing()
        return builder

    # -- validation / freeze -------------------------------------------------

    def build(self) -> Instance:
        """Validate the instance and return it, ready for use.

        Raises
        ------
        InstanceValidationError
            Listing every problem found: missing depot, stop count outside
            ``[MIN_STOPS, MAX_STOPS]``, a stop/depot referencing a node no
            longer in the graph, or the depot sharing a node with a stop.
            Two stops sharing a node is a softer problem — logged as a
            warning (see :meth:`_warn_if_node_collision`), not an error.
        """
        errors: list[str] = []
        graph_nodes = self.graph.nodes

        if self.instance.depot is None:
            errors.append("no depot set — call set_depot_from_* first")
        elif self.instance.depot.node not in graph_nodes:
            errors.append(
                f"depot node {self.instance.depot.node} is not in the current graph "
                "— rebuild the network or re-set the depot"
            )

        n = self.instance.n_stops
        if n < MIN_STOPS:
            errors.append(f"instance has {n} stops; at least {MIN_STOPS} required")
        if n > MAX_STOPS:
            errors.append(f"instance has {n} stops; at most {MAX_STOPS} allowed")

        for stop in self.instance.stops:
            if stop.node not in graph_nodes:
                errors.append(
                    f"stop {stop.id} ({stop.label!r}) node {stop.node} is not in the current "
                    "graph — rebuild the network or re-add this stop"
                )

        if self.instance.depot is not None:
            colliding = [s.id for s in self.instance.stops if s.node == self.instance.depot.node]
            if colliding:
                errors.append(
                    f"depot and stop(s) {colliding} share graph node {self.instance.depot.node} "
                    "— move the depot or the stop(s)"
                )

        if errors:
            raise InstanceValidationError(errors)

        # Mutual reachability between every stop and the depot is not
        # re-checked pairwise here: every node came from the graph's
        # largest strongly connected component (Stage 1's build_graph), so
        # it is structural, not a per-instance property, and an O(N^2)
        # pairwise check would be expensive for no benefit on a healthy
        # graph. A *disrupted* graph view (Stage 5) is a different
        # question — that stage's own connectivity check reports which
        # stops became unreachable as a first-class outcome, not by
        # reusing this method.
        return self.instance
