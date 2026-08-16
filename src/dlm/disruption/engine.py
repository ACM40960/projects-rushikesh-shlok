"""Apply a ``Scenario`` to a graph without mutating the base graph.

:func:`apply_scenario` returns a :class:`DisruptionResult` holding a
**copy** of the graph with the scenario's disruptions applied, plus a full
audit (:class:`EdgeChange` per affected edge) of exactly what changed and
which ``Disruption`` caused it. :meth:`DisruptionResult.revert` undoes
those changes on that copy in place, cheaper than re-copying the whole
graph — useful when the same disrupted view is toggled on/off repeatedly
(Stage 10's scenario-authoring UI) rather than built once and discarded.

**Resolution happens once, up front, against the undisrupted graph.**
Every disruption's shape (edge/node/corridor/polygon) is resolved to a
concrete set of ``(u, v, key)`` edges *before* any edge is removed or
slowed, so results never depend on the order disruptions are listed in —
a corridor's shortest-path resolution can't be silently rerouted by an
earlier disruption in the same scenario having already removed part of
the path it would have used.

**Closures always beat slow zones on the same edge; first-listed wins
within the same effect.** If two disruptions in one scenario target the
same edge, applying both effects (e.g. compounding two slow-zone
multipliers) would be surprising and order-fragile. Closures are applied
first, in listed order (a later closure on an already-closed edge is a
no-op); slow zones are applied second and skip any edge a closure already
removed. This is documented, not silent — see
``docs/stages/stage-05-disruptions.md``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Literal

import networkx as nx
import numpy as np
import shapely
from shapely.geometry import Polygon

from dlm.disruption.schema import Disruption, DisruptionEffect, DisruptionShape, Scenario
from dlm.network.snapping import SnapError, snap_to_node

logger = logging.getLogger(__name__)

EdgeKey = tuple[int, int, int]


class DisruptionResolutionError(ValueError):
    """Raised when a disruption's geometry cannot be resolved against a
    specific graph: a lat/lon too far from any road, a node id that
    doesn't exist in this graph, or corridor waypoints with no path
    between them."""

    def __init__(self, disruption_id: str, reason: str) -> None:
        self.disruption_id = disruption_id
        self.reason = reason
        super().__init__(f"disruption {disruption_id!r}: {reason}")


@dataclass(frozen=True)
class EdgeChange:
    """One edge's state before/after a disruption was applied."""

    u: int
    v: int
    key: int
    disruption_id: str
    kind: Literal["removed", "slowed"]
    original_attrs: dict
    """Full original edge attribute dict — only populated for "removed"
    (needed to re-``add_edge`` on revert); empty for "slowed" (only
    ``travel_time`` changed, restored from ``original_travel_time_s``)."""
    original_travel_time_s: float
    new_travel_time_s: float | None
    """``None`` for "removed" (the edge no longer has a travel time)."""


@dataclass
class DisruptionResult:
    """The outcome of applying a ``Scenario`` to a graph.

    ``graph`` is a copy — the graph passed to :func:`apply_scenario` is
    never mutated.
    """

    graph: nx.MultiDiGraph
    scenario: Scenario
    changes: list[EdgeChange] = field(default_factory=list)

    @property
    def n_edges_closed(self) -> int:
        return sum(1 for c in self.changes if c.kind == "removed")

    @property
    def n_edges_slowed(self) -> int:
        return sum(1 for c in self.changes if c.kind == "slowed")

    @property
    def affected_edges(self) -> set[EdgeKey]:
        return {(c.u, c.v, c.key) for c in self.changes}

    def revert(self) -> None:
        """Undo every change in ``changes`` on ``self.graph``, in place,
        restoring it to its pre-disruption state, and clear ``changes``.
        Touches only the edges this application changed — not a full
        re-copy of the graph.
        """
        for change in self.changes:
            if change.kind == "removed":
                self.graph.add_edge(change.u, change.v, key=change.key, **change.original_attrs)
            else:
                self.graph[change.u][change.v][change.key]["travel_time"] = (
                    change.original_travel_time_s
                )
        self.changes.clear()


def _resolve_edge_shape(graph: nx.MultiDiGraph, d: Disruption) -> set[EdgeKey]:
    if d.from_node is not None:
        u, v = d.from_node, d.to_node
        for node in (u, v):
            if node not in graph:
                raise DisruptionResolutionError(d.id, f"node {node} not in graph")
    else:
        try:
            u = snap_to_node(graph, *d.from_latlon).node
            v = snap_to_node(graph, *d.to_latlon).node
        except SnapError as exc:
            raise DisruptionResolutionError(d.id, str(exc)) from exc

    keys: set[EdgeKey] = set()
    if d.directions in ("both", "forward") and graph.has_edge(u, v):
        keys.update((u, v, k) for k in graph[u][v])
    if d.directions in ("both", "reverse") and graph.has_edge(v, u):
        keys.update((v, u, k) for k in graph[v][u])
    if not keys:
        raise DisruptionResolutionError(d.id, f"no edge between nodes {u} and {v} in this graph")
    return keys


def _resolve_node_shape(graph: nx.MultiDiGraph, d: Disruption) -> set[EdgeKey]:
    if d.node is not None:
        node = d.node
        if node not in graph:
            raise DisruptionResolutionError(d.id, f"node {node} not in graph")
    else:
        try:
            node = snap_to_node(graph, *d.at).node
        except SnapError as exc:
            raise DisruptionResolutionError(d.id, str(exc)) from exc

    keys: set[EdgeKey] = set()
    keys.update((node, v, k) for _, v, k in graph.out_edges(node, keys=True))
    keys.update((u, node, k) for u, _, k in graph.in_edges(node, keys=True))
    return keys


def _resolve_corridor_shape(graph: nx.MultiDiGraph, d: Disruption) -> set[EdgeKey]:
    nodes = []
    for lat, lon in d.waypoints:
        try:
            nodes.append(snap_to_node(graph, lat, lon).node)
        except SnapError as exc:
            raise DisruptionResolutionError(d.id, str(exc)) from exc

    keys: set[EdgeKey] = set()
    for a, b in zip(nodes[:-1], nodes[1:], strict=True):
        try:
            path = nx.shortest_path(graph, a, b, weight="travel_time")
        except nx.NetworkXNoPath as exc:
            raise DisruptionResolutionError(
                d.id, f"no path between waypoint nodes {a} and {b} on the undisrupted graph"
            ) from exc
        for u, v in zip(path[:-1], path[1:], strict=True):
            keys.update((u, v, k) for k in graph[u][v])
            if graph.has_edge(v, u):
                keys.update((v, u, k) for k in graph[v][u])
    return keys


def _resolve_polygon_shape(graph: nx.MultiDiGraph, d: Disruption) -> set[EdgeKey]:
    """An edge is affected if either endpoint falls inside the polygon —
    tested by classifying every *node* once with a single vectorised
    ``shapely.contains_xy`` call (fast: one call over all nodes) rather
    than building a Shapely point per edge (slow: repeats work for every
    node's incident edges, of which there are several)."""
    poly = Polygon([(lon, lat) for lat, lon in d.boundary])
    node_ids = list(graph.nodes)
    xs = np.array([graph.nodes[n]["x"] for n in node_ids])
    ys = np.array([graph.nodes[n]["y"] for n in node_ids])
    inside = shapely.contains_xy(poly, xs, ys)
    inside_nodes = {n for n, is_in in zip(node_ids, inside, strict=True) if is_in}

    keys: set[EdgeKey] = set()
    for u, v, k in graph.edges(keys=True):
        if u in inside_nodes or v in inside_nodes:
            keys.add((u, v, k))
    return keys


def _resolve_shape(graph: nx.MultiDiGraph, d: Disruption) -> set[EdgeKey]:
    if d.shape is DisruptionShape.EDGE:
        return _resolve_edge_shape(graph, d)
    if d.shape is DisruptionShape.NODE:
        return _resolve_node_shape(graph, d)
    if d.shape is DisruptionShape.CORRIDOR:
        return _resolve_corridor_shape(graph, d)
    return _resolve_polygon_shape(graph, d)


def _is_active(d: Disruption, at_time: float | None) -> bool:
    if at_time is None or d.time_window is None:
        return True
    start, end = d.time_window
    return start <= at_time < end


def apply_scenario(
    graph: nx.MultiDiGraph,
    scenario: Scenario,
    at_time: float | None = None,
) -> DisruptionResult:
    """Apply every currently-active disruption in `scenario` to a copy of
    `graph`. `graph` itself is never modified.

    Parameters
    ----------
    graph : nx.MultiDiGraph
        The base graph. Not mutated.
    scenario : Scenario
    at_time : float, optional
        Simulated seconds since scenario start. A disruption with a
        `time_window` not covering `at_time` is skipped. `None` (default)
        applies every disruption regardless of time window.

    Returns
    -------
    DisruptionResult

    Raises
    ------
    DisruptionResolutionError
        If any active disruption's geometry cannot be resolved against
        `graph` (see :func:`_resolve_shape`). Raised before any edge is
        touched, so a resolution failure never leaves a partially-applied
        graph.
    """
    active = [d for d in scenario.disruptions if _is_active(d, at_time)]
    resolved = [(d, _resolve_shape(graph, d)) for d in active]

    disrupted = graph.copy()
    changes: list[EdgeChange] = []

    closures = [(d, keys) for d, keys in resolved if d.effect is DisruptionEffect.CLOSURE]
    slow_zones = [(d, keys) for d, keys in resolved if d.effect is DisruptionEffect.SLOW_ZONE]

    for d, edge_keys in closures:
        for u, v, key in sorted(edge_keys):
            if not disrupted.has_edge(u, v, key):
                logger.debug(
                    "disruption %s: edge (%s,%s,%s) already closed by an earlier disruption",
                    d.id,
                    u,
                    v,
                    key,
                )
                continue
            attrs = dict(disrupted[u][v][key])
            original_tt = attrs["travel_time"]
            disrupted.remove_edge(u, v, key)
            changes.append(EdgeChange(u, v, key, d.id, "removed", attrs, original_tt, None))

    slowed: set[EdgeKey] = set()
    for d, edge_keys in slow_zones:
        for u, v, key in sorted(edge_keys):
            if not disrupted.has_edge(u, v, key):
                logger.debug(
                    "disruption %s: edge (%s,%s,%s) closed elsewhere in this scenario, "
                    "closure wins over slow zone",
                    d.id,
                    u,
                    v,
                    key,
                )
                continue
            if (u, v, key) in slowed:
                logger.debug(
                    "disruption %s: edge (%s,%s,%s) already slowed by an earlier "
                    "disruption in this scenario, first wins",
                    d.id,
                    u,
                    v,
                    key,
                )
                continue
            edge_attrs = disrupted[u][v][key]
            original_tt = edge_attrs["travel_time"]
            new_tt = original_tt / d.speed_factor
            edge_attrs["travel_time"] = new_tt
            changes.append(EdgeChange(u, v, key, d.id, "slowed", {}, original_tt, new_tt))
            slowed.add((u, v, key))

    return DisruptionResult(graph=disrupted, scenario=scenario, changes=changes)


@dataclass
class ScenarioValidation:
    """Report from :func:`validate_scenario`: whether every disruption in
    a scenario resolves cleanly against a specific graph."""

    valid: bool
    n_disruptions: int
    errors: list[str]
    resolved_edge_counts: dict[str, int]


def validate_scenario(graph: nx.MultiDiGraph, scenario: Scenario) -> ScenarioValidation:
    """Resolve every disruption's geometry against `graph` without
    applying any effect — for `dlm disrupt validate`, so a scenario
    author finds a bad lat/lon or an unreachable corridor before ever
    running an experiment against it.
    """
    errors: list[str] = []
    counts: dict[str, int] = {}
    for d in scenario.disruptions:
        try:
            keys = _resolve_shape(graph, d)
        except DisruptionResolutionError as exc:
            errors.append(str(exc))
            continue
        counts[d.id] = len(keys)
        if not keys:
            errors.append(
                f"disruption {d.id!r} resolved to zero edges "
                "(an isolated node, or a polygon with no nodes inside it)"
            )
    return ScenarioValidation(
        valid=not errors,
        n_disruptions=len(scenario.disruptions),
        errors=errors,
        resolved_edge_counts=counts,
    )
