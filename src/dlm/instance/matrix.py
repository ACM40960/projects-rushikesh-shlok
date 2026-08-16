"""Cached, incrementally-updatable travel-time + path matrix.

Gives O(1) lookup of the cost, the full node-sequence path, and the
distance between any two points of an instance (depot + stops), built
with one Dijkstra per point (not one per *pair* — an N-point matrix costs
N Dijkstras, not N^2).

Adding one point to an existing matrix costs exactly two more Dijkstras
(one each direction, since the graph is directed) — not a full rebuild.
This is what lets ``InstanceBuilder.add_stop_from_*`` stay cheap as an
instance grows: see :meth:`Matrix.add_point`.
"""

from __future__ import annotations

import hashlib
import logging
import pickle
import time
from dataclasses import dataclass, field
from pathlib import Path

import networkx as nx

from dlm.config import settings

logger = logging.getLogger(__name__)

DEFAULT_WEIGHT = "travel_time"
_TRIANGLE_INEQUALITY_TOLERANCE_S = 1e-6
_MATRIX_SCHEMA_VERSION = 2  # bump to invalidate stale caches on a schema change


@dataclass
class MatrixStats:
    """Coverage/sanity report for a built matrix, for `dlm instance matrix`
    and the stage docs.

    Attributes
    ----------
    n_points : int
        Number of points in the matrix (depot + stops).
    n_ordered_pairs : int
        `n_points * (n_points - 1)` — every (i, j) with i != j.
    asymmetric_pairs : int
        Unordered pairs {i, j} where cost(i, j) != cost(j, i). Expected to
        be non-zero on a real directed road network (one-way streets).
    asymmetry_rate : float
        `asymmetric_pairs / (n_points * (n_points - 1) / 2)`.
    triangle_violations : int
        Ordered triples (i, j, k) where cost(i, k) > cost(i, j) + cost(j, k)
        beyond floating-point tolerance. Expected to be exactly zero for a
        correctly computed shortest-path matrix on a single static graph
        (see docs/stages/stage-03-matrix.md for why this is a correctness
        guarantee, not a coincidence) — reported so this stays a visible,
        checked assertion rather than an assumption.
    build_seconds : float
        Wall-clock time for this build (or cache load).
    from_cache : bool
        True if loaded from disk rather than computed.
    """

    n_points: int
    n_ordered_pairs: int
    asymmetric_pairs: int
    asymmetry_rate: float
    triangle_violations: int
    build_seconds: float
    from_cache: bool


@dataclass
class Matrix:
    """All-pairs travel-time + path lookup over a fixed set of graph nodes.

    Attributes
    ----------
    nodes : list[int]
        The points this matrix covers, in insertion order.
    weight : str
        The edge attribute Dijkstra minimises — `"travel_time"` by default.
    cost : dict[tuple[int, int], float]
        `cost[(u, v)]` is the shortest travel time from `u` to `v`, seconds
        (i.e. the path Dijkstra found *minimising* travel time).
    paths : dict[tuple[int, int], list[int]]
        `paths[(u, v)]` is the full node sequence from `u` to `v`
        (inclusive of both endpoints).
    distance : dict[tuple[int, int], float]
        `distance[(u, v)]` is the metres travelled along that same
        shortest (by time) path — not an independent shortest-by-distance
        computation. Stored alongside `cost`/`paths` (rather than requiring
        solvers to also hold a graph reference) so `Solver.solve(instance,
        matrix)` is self-contained, per the brief's protocol.
    """

    nodes: list[int] = field(default_factory=list)
    weight: str = DEFAULT_WEIGHT
    cost: dict[tuple[int, int], float] = field(default_factory=dict)
    paths: dict[tuple[int, int], list[int]] = field(default_factory=dict)
    distance: dict[tuple[int, int], float] = field(default_factory=dict)

    def get_cost(self, u: int, v: int) -> float:
        """Shortest travel time from `u` to `v`, in seconds."""
        return self.cost[(u, v)]

    def get_path(self, u: int, v: int) -> list[int]:
        """Full node sequence of the shortest path from `u` to `v`."""
        return self.paths[(u, v)]

    def get_distance(self, u: int, v: int) -> float:
        """Metres travelled along the shortest (by time) path from `u` to `v`."""
        return self.distance[(u, v)]

    def add_point(self, graph: nx.MultiDiGraph, node: int) -> None:
        """Add one point, costing exactly two Dijkstras (one per direction)
        regardless of how many points are already in the matrix.

        Mutates this matrix in place.
        """
        dist_out, paths_out = nx.single_source_dijkstra(graph, node, weight=self.weight)

        reverse_view = graph.reverse(copy=False)
        dist_in, paths_in_rev = nx.single_source_dijkstra(reverse_view, node, weight=self.weight)

        for other in self.nodes:
            self.cost[(node, other)] = dist_out[other]
            self.paths[(node, other)] = paths_out[other]
            self.distance[(node, other)] = _path_distance_m(graph, paths_out[other])

            path_in = list(reversed(paths_in_rev[other]))
            self.cost[(other, node)] = dist_in[other]
            self.paths[(other, node)] = path_in
            self.distance[(other, node)] = _path_distance_m(graph, path_in)

        self.cost[(node, node)] = 0.0
        self.paths[(node, node)] = [node]
        self.distance[(node, node)] = 0.0
        self.nodes.append(node)

    def remove_point(self, node: int) -> None:
        """Drop a point and its row/column. No recomputation."""
        self.nodes.remove(node)
        for other in [*self.nodes, node]:
            self.cost.pop((node, other), None)
            self.cost.pop((other, node), None)
            self.paths.pop((node, other), None)
            self.paths.pop((other, node), None)
            self.distance.pop((node, other), None)
            self.distance.pop((other, node), None)

    def move_point(self, graph: nx.MultiDiGraph, old_node: int, new_node: int) -> None:
        """Equivalent to `remove_point(old_node)` then `add_point(new_node)`."""
        self.remove_point(old_node)
        self.add_point(graph, new_node)

    def recompute_on(self, graph: nx.MultiDiGraph) -> Matrix:
        """Rebuild this matrix's exact point set against a *different*
        graph (e.g. a disrupted graph view, Stage 5), as a new `Matrix`.

        Does not touch the disk cache: a disrupted view is scenario- and
        moment-specific, not something to conflate with the base graph's
        cache.
        """
        return _build(graph, list(self.nodes), self.weight)

    def stats(self, build_seconds: float = 0.0, from_cache: bool = False) -> MatrixStats:
        """Compute the asymmetry/triangle-inequality sanity report."""
        return _compute_stats(self, build_seconds, from_cache)

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("wb") as f:
            pickle.dump(self, f, protocol=pickle.HIGHEST_PROTOCOL)

    @classmethod
    def load(cls, path: Path) -> Matrix:
        with path.open("rb") as f:
            return pickle.load(f)  # noqa: S301 - our own cache, never untrusted input


def _path_distance_m(graph: nx.MultiDiGraph, path: list[int]) -> float:
    """Sum edge length (metres) along a node path.

    The graph is a `MultiDiGraph`: parallel edges between the same two
    nodes are possible. For each step, the edge Dijkstra would have
    actually used is the one with minimum `weight` (not necessarily
    minimum length) — so that edge's length is what's summed here, rather
    than an independent (and potentially inconsistent) minimum-length edge.
    """
    total = 0.0
    for u, v in zip(path[:-1], path[1:], strict=True):
        parallel_edges = graph.get_edge_data(u, v)
        chosen = min(parallel_edges.values(), key=lambda d: d.get("travel_time", float("inf")))
        total += chosen["length"]
    return total


def _build(graph: nx.MultiDiGraph, nodes: list[int], weight: str) -> Matrix:
    """One Dijkstra per node (not per pair): O(N) shortest-path searches
    for an N-point matrix, each yielding costs+paths to every other point
    in a single call.
    """
    m = Matrix(nodes=[], weight=weight)
    for node in nodes:
        m.add_point(graph, node)
    return m


def _compute_stats(m: Matrix, build_seconds: float, from_cache: bool) -> MatrixStats:
    n = len(m.nodes)
    n_ordered_pairs = n * (n - 1)

    asymmetric_pairs = 0
    for i in range(n):
        for j in range(i + 1, n):
            u, v = m.nodes[i], m.nodes[j]
            if m.cost[(u, v)] != m.cost[(v, u)]:
                asymmetric_pairs += 1
    n_unordered_pairs = n * (n - 1) // 2
    asymmetry_rate = asymmetric_pairs / n_unordered_pairs if n_unordered_pairs else 0.0

    triangle_violations = 0
    for i in m.nodes:
        for j in m.nodes:
            if j == i:
                continue
            cost_ij = m.cost[(i, j)]
            for k in m.nodes:
                if k == i or k == j:
                    continue
                if m.cost[(i, k)] > cost_ij + m.cost[(j, k)] + _TRIANGLE_INEQUALITY_TOLERANCE_S:
                    triangle_violations += 1

    return MatrixStats(
        n_points=n,
        n_ordered_pairs=n_ordered_pairs,
        asymmetric_pairs=asymmetric_pairs,
        asymmetry_rate=asymmetry_rate,
        triangle_violations=triangle_violations,
        build_seconds=build_seconds,
        from_cache=from_cache,
    )


def _cache_path(graph_id: str, nodes: list[int], weight: str) -> Path:
    payload = f"v{_MATRIX_SCHEMA_VERSION}|{graph_id}|{sorted(nodes)}|{weight}"
    key = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
    return settings.cache_dir / "matrix" / f"{key}.pkl"


def build_matrix(
    graph: nx.MultiDiGraph,
    nodes: list[int],
    graph_id: str,
    weight: str = DEFAULT_WEIGHT,
    force_rebuild: bool = False,
) -> tuple[Matrix, MatrixStats]:
    """Load a matrix from cache, or build and cache it.

    Parameters
    ----------
    graph : nx.MultiDiGraph
        The graph to compute shortest paths on.
    nodes : list[int]
        The points to cover — typically an instance's depot + stop nodes.
    graph_id : str
        Identifies which graph this is, for the cache key (e.g.
        `GraphBuildReport.cache_path.stem` from `dlm.network.loader`).
        Two instances built on the same graph with the same point set
        share a cache entry, regardless of instance name.
    weight : str, default "travel_time"
        Edge attribute to minimise.
    force_rebuild : bool
        If True, ignore any cached matrix and recompute.

    Returns
    -------
    (Matrix, MatrixStats)
    """
    settings.ensure_dirs()
    cache_path = _cache_path(graph_id, nodes, weight)
    t0 = time.time()

    if cache_path.exists() and not force_rebuild:
        m = Matrix.load(cache_path)
        build_seconds = time.time() - t0
        stats = m.stats(build_seconds=build_seconds, from_cache=True)
        logger.info("loaded matrix from cache in %.3fs: %s", build_seconds, cache_path)
        return m, stats

    m = _build(graph, nodes, weight)
    build_seconds = time.time() - t0
    m.save(cache_path)
    stats = m.stats(build_seconds=build_seconds, from_cache=False)
    logger.info(
        "built matrix for %d points in %.3fs (asymmetry %.1f%%, %d triangle violations); "
        "cached to %s",
        stats.n_points,
        build_seconds,
        100 * stats.asymmetry_rate,
        stats.triangle_violations,
        cache_path,
    )
    return m, stats
