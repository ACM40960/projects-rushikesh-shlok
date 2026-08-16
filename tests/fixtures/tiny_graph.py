"""A tiny, hand-built directed graph with known lengths/speeds, used to
unit-test travel_time imputation, snapping, and (in later stages) the
matrix and solver code without touching the real Dublin network or the
network.

Node layout (WGS84 degrees, central Dublin-ish coordinates, not real
streets):

    1 --50kph(real)--> 2 --imputed(primary)--> 3
    2 --50kph(real)--> 1        3 --imputed(primary)--> 2
    1 --imputed(service)--> 4   4 --30mph(real)--> 3
    3 --imputed(service)--> 5   (5 has no outgoing edge: a dead-end
                                  weakly-attached node, dropped by
                                  largest strongly-connected-component
                                  extraction)

Edge 1->4 has no reverse (4->1): this is the fixture's one-way street.
"""

from __future__ import annotations

import networkx as nx

NODES: dict[int, dict[str, float]] = {
    1: {"y": 53.3400, "x": -6.2700},
    2: {"y": 53.3410, "x": -6.2690},
    3: {"y": 53.3420, "x": -6.2680},
    4: {"y": 53.3400, "x": -6.2680},
    5: {"y": 53.3430, "x": -6.2670},
}

# (u, v, length_m, highway, maxspeed)
EDGES: list[tuple[int, int, float, str, str | None]] = [
    (1, 2, 100.0, "residential", "50"),
    (2, 1, 100.0, "residential", "50"),
    (2, 3, 150.0, "primary", None),
    (3, 2, 150.0, "primary", None),
    (1, 4, 120.0, "service", None),  # one-way: no reverse edge
    (4, 3, 130.0, "tertiary", "30 mph"),
    (3, 5, 90.0, "service", None),  # dead end: 5 has no outgoing edge
]

# Expected speed_kph the real (non-imputed) edges resolve to.
EXPECTED_REAL_SPEED_KPH = {
    (1, 2): 50.0,
    (2, 1): 50.0,
    (4, 3): 30.0 * 1.609344,
}

MAX_SNAP_DIST_M = 150.0


def make_tiny_graph() -> nx.MultiDiGraph:
    """Build the fixture graph described in this module's docstring."""
    G = nx.MultiDiGraph(crs="epsg:4326")
    for node_id, attrs in NODES.items():
        G.add_node(node_id, **attrs)
    for u, v, length_m, highway, maxspeed in EDGES:
        attrs: dict[str, object] = {"length": length_m, "highway": highway}
        if maxspeed is not None:
            attrs["maxspeed"] = maxspeed
        G.add_edge(u, v, **attrs)
    return G
