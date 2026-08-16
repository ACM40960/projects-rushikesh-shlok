"""Interactive Folium maps: route layers, disruption layers, before/after.

Stage 2 introduces :func:`render_instance_map` (``dlm instance map``) so
stop selection is visually checkable before any UI exists. Route and
disruption layers are added in Stages 4-6.
"""

from __future__ import annotations

from pathlib import Path

import folium

from dlm.instance.schema import Instance

_DEPOT_COLOR = "black"
_STOP_COLOR = "blue"


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
