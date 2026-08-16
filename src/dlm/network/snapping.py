"""Snap a lat/lon point to the nearest routable graph node.

This is the safety boundary between free-form user input (map clicks,
geocoded addresses, typed lat/lon) and the graph: every such input passes
through :func:`snap_to_node` before it can become a depot or a stop. A
point that is nowhere near the road network (a park with no driveable
road, a point out in Dublin Bay) must fail loudly and readably here,
rather than silently snapping to a node hundreds of metres away and
producing a plausible-looking but wrong route.
"""

from __future__ import annotations

from dataclasses import dataclass

import networkx as nx
import osmnx as ox


class SnapError(ValueError):
    """Raised when a point cannot be snapped to a routable node nearby.

    Carries the query point and the distance to the nearest node (if any
    node exists at all) so the caller can show a precise, human-readable
    message instead of a generic failure.
    """

    def __init__(self, lat: float, lon: float, nearest_dist_m: float, max_dist_m: float) -> None:
        self.lat = lat
        self.lon = lon
        self.nearest_dist_m = nearest_dist_m
        self.max_dist_m = max_dist_m
        super().__init__(
            f"No routable road within {max_dist_m:.0f} m of ({lat:.5f}, {lon:.5f}) "
            f"— nearest node is {nearest_dist_m:.0f} m away. This point is likely "
            "off the road network (e.g. water, a park, or outside the loaded map "
            "area). Choose a point closer to a real street."
        )


@dataclass(frozen=True)
class SnapResult:
    """The outcome of successfully snapping a point to the graph.

    Attributes
    ----------
    node : int
        The OSM node ID of the nearest routable node.
    lat, lon : float
        The original query coordinates.
    dist_m : float
        Distance from the query point to the snapped node, in metres.
    """

    node: int
    lat: float
    lon: float
    dist_m: float


DEFAULT_MAX_SNAP_DIST_M = 150.0


def snap_to_node(
    G: nx.MultiDiGraph,
    lat: float,
    lon: float,
    max_dist_m: float = DEFAULT_MAX_SNAP_DIST_M,
) -> SnapResult:
    """Find the nearest routable node to a (lat, lon) point.

    Parameters
    ----------
    G : nx.MultiDiGraph
        An unprojected (EPSG:4326) routable graph, as produced by
        ``dlm.network.loader``.
    lat, lon : float
        Query point in WGS84 decimal degrees.
    max_dist_m : float, default 150.0
        Maximum allowed distance between the query point and the nearest
        node. Chosen to comfortably cover "clicked slightly off the road
        centreline" while still rejecting points nowhere near a street.

    Returns
    -------
    SnapResult

    Raises
    ------
    SnapError
        If the nearest node is farther than `max_dist_m` away.
    """
    node, dist_m = ox.distance.nearest_nodes(G, X=lon, Y=lat, return_dist=True)
    if dist_m > max_dist_m:
        raise SnapError(lat=lat, lon=lon, nearest_dist_m=float(dist_m), max_dist_m=max_dist_m)
    return SnapResult(node=int(node), lat=lat, lon=lon, dist_m=float(dist_m))
