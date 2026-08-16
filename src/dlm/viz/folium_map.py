"""Interactive Folium maps: route layers, disruption layers, before/after.

Stage 2 introduces :func:`render_instance_map` (``dlm instance map``) so
stop selection is visually checkable before any UI exists. Stage 4 adds
:func:`render_route_map` (``dlm plan``), drawing the solved route's actual
street-following geometry. Stage 5 adds :func:`render_disruption_map`
(``dlm disrupt preview``). Before/after comparison layers land in Stage 6.
"""

from __future__ import annotations

from pathlib import Path

import folium
import networkx as nx

from dlm.disruption.engine import DisruptionResult
from dlm.instance.schema import Instance
from dlm.solver.base import Solution

_DEPOT_COLOR = "black"
_STOP_COLOR = "blue"
_ROUTE_COLOR = "#2c7fb8"
_CLOSED_COLOR = "#d7191c"
_SLOWED_COLOR = "#fd8d3c"
_DUBLIN_CENTRE = (53.3498, -6.2603)


def render_instance_map(instance: Instance) -> folium.Map:
    """Render a standalone Folium map of an instance's depot and stops.

    Parameters
    ----------
    instance : Instance
        Must have a depot set (used as the map centre); stops may be empty.

    Returns
    -------
    folium.Map
    """
    if instance.depot is None:
        raise ValueError(
            f"Instance {instance.name!r} has no depot set — cannot centre a map without one."
        )

    m = folium.Map(location=[instance.depot.lat, instance.depot.lon], zoom_start=13)

    folium.Marker(
        location=[instance.depot.lat, instance.depot.lon],
        popup=f"Depot: {instance.depot.label} (node {instance.depot.node})",
        tooltip=f"Depot: {instance.depot.label}",
        icon=folium.Icon(color=_DEPOT_COLOR, icon="home"),
    ).add_to(m)

    for i, stop in enumerate(instance.stops, start=1):
        folium.Marker(
            location=[stop.lat, stop.lon],
            popup=(f"{stop.id}: {stop.label} (node {stop.node}, source={stop.source.value})"),
            tooltip=f"{i}. {stop.label}",
            icon=folium.Icon(color=_STOP_COLOR, icon="info-sign"),
        ).add_to(m)

    if instance.stops:
        all_lats = [instance.depot.lat, *(s.lat for s in instance.stops)]
        all_lons = [instance.depot.lon, *(s.lon for s in instance.stops)]
        m.fit_bounds([[min(all_lats), min(all_lons)], [max(all_lats), max(all_lons)]])

    return m


def save_instance_map(instance: Instance, path: Path) -> Path:
    """Render and save an instance map as a standalone HTML file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    m = render_instance_map(instance)
    m.save(str(path))
    return path


def render_route_map(instance: Instance, solution: Solution, graph: nx.MultiDiGraph) -> folium.Map:
    """Render a solved route: depot + stops, numbered by visit order, with
    each leg drawn as its actual street-following polyline (not a straight
    line between endpoints) using the leg's full node path.

    Parameters
    ----------
    instance : Instance
        The instance `solution` was solved against.
    solution : Solution
        A solved route (e.g. from `TwoOptSolver`).
    graph : nx.MultiDiGraph
        The road graph `solution`'s node ids belong to, for path -> lat/lon.
    """
    if instance.depot is None:
        raise ValueError(
            f"Instance {instance.name!r} has no depot set — cannot render a route map."
        )

    m = folium.Map(location=[instance.depot.lat, instance.depot.lon], zoom_start=13)

    for leg in solution.legs:
        coords = [(graph.nodes[n]["y"], graph.nodes[n]["x"]) for n in leg.path]
        folium.PolyLine(
            coords,
            color=_ROUTE_COLOR,
            weight=4,
            opacity=0.8,
            tooltip=(
                f"{leg.from_id} -> {leg.to_id}: {leg.travel_time_s:.0f}s, {leg.distance_m:.0f}m"
            ),
        ).add_to(m)

    folium.Marker(
        location=[instance.depot.lat, instance.depot.lon],
        popup=f"Depot: {instance.depot.label} (node {instance.depot.node})",
        tooltip=f"Depot: {instance.depot.label}",
        icon=folium.Icon(color=_DEPOT_COLOR, icon="home"),
    ).add_to(m)

    stops_by_id = {s.id: s for s in instance.stops}
    for visit_number, stop_id in enumerate(solution.order, start=1):
        stop = stops_by_id[stop_id]
        folium.Marker(
            location=[stop.lat, stop.lon],
            popup=f"Stop {visit_number}: {stop.id} {stop.label} (node {stop.node})",
            tooltip=f"{visit_number}. {stop.label}",
            icon=folium.Icon(color=_STOP_COLOR, icon="info-sign"),
        ).add_to(m)

    all_lats = [instance.depot.lat, *(s.lat for s in stops_by_id.values())]
    all_lons = [instance.depot.lon, *(s.lon for s in stops_by_id.values())]
    m.fit_bounds([[min(all_lats), min(all_lons)], [max(all_lats), max(all_lons)]])

    return m


def save_route_map(
    instance: Instance, solution: Solution, graph: nx.MultiDiGraph, path: Path
) -> Path:
    """Render and save a route map as a standalone HTML file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    m = render_route_map(instance, solution, graph)
    m.save(str(path))
    return path


def _edge_coords(graph: nx.MultiDiGraph, u: int, v: int, attrs: dict) -> list[tuple[float, float]]:
    """(lat, lon) points to draw for edge (u, v) given its attribute dict:
    its real OSM geometry where present (most edges — see
    `dlm.network.loader`), else a straight line between the two endpoint
    nodes."""
    if "geometry" in attrs:
        return [(lat, lon) for lon, lat in attrs["geometry"].coords]
    return [(graph.nodes[u]["y"], graph.nodes[u]["x"]), (graph.nodes[v]["y"], graph.nodes[v]["x"])]


def render_disruption_map(result: DisruptionResult) -> folium.Map:
    """Render a Folium map highlighting one scenario application's effect:
    closed edges in red, slowed edges in orange, each drawn along its real
    street geometry (not a straight line between endpoints).

    Node coordinates come from `result.graph` — safe even for closed edges,
    since removing an edge never removes its endpoint nodes.
    """
    graph = result.graph
    if result.changes:
        lats = [graph.nodes[n]["y"] for c in result.changes for n in (c.u, c.v)]
        lons = [graph.nodes[n]["x"] for c in result.changes for n in (c.u, c.v)]
        center = (sum(lats) / len(lats), sum(lons) / len(lons))
    else:
        center = _DUBLIN_CENTRE

    m = folium.Map(location=list(center), zoom_start=15)

    for change in result.changes:
        if change.kind == "removed":
            attrs = change.original_attrs
            color = _CLOSED_COLOR
            tooltip = f"CLOSED ({change.disruption_id})"
        else:
            attrs = graph[change.u][change.v][change.key]
            color = _SLOWED_COLOR
            tooltip = (
                f"SLOWED {change.original_travel_time_s:.0f}s -> "
                f"{change.new_travel_time_s:.0f}s ({change.disruption_id})"
            )
        coords = _edge_coords(graph, change.u, change.v, attrs)
        folium.PolyLine(coords, color=color, weight=5, opacity=0.9, tooltip=tooltip).add_to(m)

    all_lats = [graph.nodes[n]["y"] for c in result.changes for n in (c.u, c.v)]
    all_lons = [graph.nodes[n]["x"] for c in result.changes for n in (c.u, c.v)]
    if all_lats:
        m.fit_bounds([[min(all_lats), min(all_lons)], [max(all_lats), max(all_lons)]])

    return m


def save_disruption_map(result: DisruptionResult, path: Path) -> Path:
    """Render and save a disruption map as a standalone HTML file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    m = render_disruption_map(result)
    m.save(str(path))
    return path
