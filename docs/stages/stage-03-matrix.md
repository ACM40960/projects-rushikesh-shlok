# Stage 03 — Travel-time matrix

## Goal

Fast, cached `O(1)` lookup of both the cost *and* the full path between any
two points of an instance — and cheap to keep in sync as the user adds,
removes, or moves stops. Before this stage, answering "how long from stop
3 to stop 7" meant running Dijkstra on the full ~28,000-node Dublin graph
on demand; after it, that's one dictionary lookup, with the underlying
Dijkstra work paid once and cached.

## Scope

**In scope:**
- `Matrix`: all-pairs cost + path lookup over an explicit set of graph
  nodes (an instance's depot + stops).
- Incremental `add_point`/`remove_point`/`move_point` — adding one point
  costs two Dijkstras, never a full rebuild.
- `recompute_on(graph)` — rebuild the same point set against a different
  graph (for Stage 5's disrupted views), without touching the disk cache.
- Disk cache keyed by `(graph identity, sorted node set, weight attribute)`,
  shared across instances that happen to use the same points.
- `dlm instance matrix` CLI command.

**Explicitly out of scope** (land in the stage noted):
- Any solver consuming the matrix — Stage 4.
- Rebuilding the matrix on a disrupted graph as part of an actual
  experiment (this stage only builds the *mechanism*, `recompute_on`;
  Stage 5/6 are what call it with a real disrupted graph).

## Design

**One Dijkstra per point, not one per pair.** `nx.single_source_dijkstra(graph, node, weight=...)`
returns costs *and* paths to every reachable node in one call. Building an
`N`-point matrix therefore costs `N` calls (each `O(E log V)` on the full
graph), not `N²` — see `_build` in `dlm.instance.matrix`. This is what
makes both the initial build and, more importantly, the incremental update
cheap.

**Adding one point costs exactly two Dijkstras, not one.** The graph is
directed (one-way streets), so the cost *from* a new point to the existing
ones and the cost *to* it from them are computed by two different
searches. The "to" direction is computed cheaply by running Dijkstra
**from** the new point on `graph.reverse(copy=False)` — a reversed *view*
(no graph copy) — which gives shortest distances *to* the new point in the
original graph in one pass, instead of running a separate single-target
search from every existing point. Paths from the reversed search are
reversed back into forward-direction node sequences before storing. See
`Matrix.add_point`.

**The matrix schema doubles as the disk cache format.** `Matrix` is a
plain dataclass (`nodes`, `weight`, `cost` dict, `paths` dict) and is
pickled directly — no separate serialisation layer, consistent with Stage
1/2's amendment to use pickle for the graph cache (ADR-0003) once graphml
proved too slow at this project's scale. A matrix is never hand-edited or
inspected outside this project, so there's no interchange requirement
pickle would violate.

**Cache key includes graph identity, not just the node set.** Two
different graph builds (e.g. before/after a bbox change, or different
OSMnx versions) must never share a matrix cache entry even if they
happen to use the same node IDs, since the same OSM node ID's shortest
paths can differ between graph versions. `graph_id` is passed in by the
caller as `GraphBuildReport.cache_path.stem` (Stage 1's own graph cache
key) rather than recomputed here, so the two caches (graph, matrix) are
guaranteed consistent by construction rather than by convention.

**Asymmetry is checked, not assumed.** `MatrixStats.asymmetry_rate` is
computed over all unordered pairs and reported by `dlm instance matrix`.
On real Dublin instances this is consistently **>95%** (see Results) —
almost every pair of points has a different cost each direction, which is
the expected signature of a genuinely directed, one-way-heavy city street
network. A matrix built on an *undirected* graph by mistake would show
`asymmetry_rate == 0.0` exactly; this number is the sanity check for that
class of bug, not just a reported statistic.

**Triangle-inequality violations are counted — and found to be exactly
zero, which is the expected, correct result, not something to force.** For
shortest-path costs computed on one static graph, `d(i,k) <= d(i,j) + d(j,k)`
holds by construction: `d(i,k)` is defined as the *minimum* over every
path from `i` to `k`, and any path through `j` (cost `d(i,j) + d(j,k)`) is
one of the paths that minimum is taken over. A violation would only be
possible if `i`, `j`, `k`'s costs came from *different* graphs, different
edge weights, or a bug in the shortest-path computation itself — so
`triangle_violations == 0` on every test in this stage (tiny fixture and
real graph alike) is reported as a positive correctness signal, checked on
every build via `MatrixStats`, not assumed and left unverified.

**Alternatives considered:**
- **`numpy` 2D array instead of a `dict[(u, v)]`** — rejected for this
  stage: point sets change (add/remove/move), and a dict keyed by real
  node IDs needs no separate index-to-node mapping and no array resizing
  logic. Revisit if Stage 7's batch experiments show the dict lookup
  itself (not the Dijkstra cost) is a bottleneck at scale — not observed
  here.
- **Caching per-pair rather than per-instance node-set** — rejected: the
  brief's own cache-key spec (`f(graph hash, sorted node set, weight)`)
  is coarser but simpler, and instances rarely share exact point sets
  across unrelated runs anyway, so per-pair caching's extra complexity
  wouldn't pay for itself.

## Interfaces

- `dlm.instance.matrix.Matrix`: `.nodes`, `.weight`, `.cost`, `.paths`;
  `get_cost(u, v)`, `get_path(u, v)`, `add_point(graph, node)`,
  `remove_point(node)`, `move_point(graph, old, new)`,
  `recompute_on(graph) -> Matrix`, `stats() -> MatrixStats`, `save(path)`,
  `Matrix.load(path)` (classmethod).
- `dlm.instance.matrix.build_matrix(graph, nodes, graph_id, weight="travel_time", force_rebuild=False) -> (Matrix, MatrixStats)`
  — the cached entry point.
- `dlm.instance.matrix.MatrixStats`: `n_points`, `n_ordered_pairs`,
  `asymmetric_pairs`, `asymmetry_rate`, `triangle_violations`,
  `build_seconds`, `from_cache`.
- CLI: `dlm instance matrix --name X [--force]`.

## Data & assumptions

- Weight attribute: `"travel_time"` (seconds), matching Stage 1's edge
  attribute — configurable via `Matrix.weight` / `build_matrix(weight=...)`
  but never changed by anything in this stage.
- Triangle-inequality tolerance: `1e-6` seconds, to absorb floating-point
  rounding noise without masking a genuine bug.
- Cache location: `data/cache/matrix/<hash>.pkl` (gitignored, like the
  graph cache).

## How to run

```bash
source .venv/bin/activate
dlm instance matrix --name small     # builds (or loads) and reports stats
dlm instance matrix --name small     # second call: cache hit, near-instant
dlm instance matrix --name medium --force   # ignore cache, rebuild
```

## Acceptance criteria

- ✅ **N=20 matrix builds in a documented, reasonable time; second call is
  cache-instant.** Evidence
  (`tests/test_matrix.py::test_n20_matrix_builds_and_caches_instantly`,
  and the same numbers reproduced via the CLI below): first build of the
  `medium` instance's 21-point matrix (N=20 stops + depot) took
  **13.9–15.5s** (21 Dijkstras against the ~28,000-node Greater Dublin
  graph — timing varies slightly run to run); the cached reload took
  **0.004s**.
- ✅ **Incremental add produces a matrix identical to a full rebuild, and
  is measurably faster (both timings reported).** Evidence
  (`test_incremental_add_matches_full_rebuild_on_real_graph`,
  `test_incremental_add_is_measurably_faster_than_full_rebuild`, and the
  fixture-based `test_incremental_add_point_matches_full_rebuild`):
  adding the 21st point to an existing 20-point matrix took **0.956s**;
  rebuilding the full 21-point matrix from scratch took **13.891s** — a
  **14.5x** speedup, with every cost and path entry byte-identical between
  the two.
- ✅ **Asymmetry rate reported and non-zero.** `small` instance (9 points):
  **97.2%** of unordered pairs have a different cost each direction;
  `medium` (21 points): **97.1%**. Both far from the `0.0%` an
  accidentally-undirected graph would produce.
- ✅ **Triangle-inequality violations counted and explained.** **Zero**
  violations, on every matrix built in this stage (tiny fixture and real
  graph alike) — see the Design section above for the mathematical reason
  this is the correct, expected result rather than a coincidence, and why
  a non-zero count would indicate a bug rather than something legitimate.
- ✅ **Unit test on a tiny fixture graph with a hand-computed matrix.**
  `tests/test_matrix.py::test_matrix_costs_match_hand_derivation` and
  `test_matrix_paths_match_hand_derivation` — every cost and path for all
  16 ordered pairs over the fixture's 4 connected nodes, derived by manual
  graph inspection (not by calling networkx a second time) and asserted
  exactly.

All 51 tests pass (`pytest -v`: 39 offline + 12 for this stage, 8 offline /
4 network-marked within it); `ruff check .` / `ruff format --check .` clean.

## Results / evidence

```
$ dlm instance matrix --name small
cache:              built fresh
build time:         6.228s
points:             9
ordered pairs:      72
asymmetric pairs:   35 (97.2%)
triangle violations: 0

$ dlm instance matrix --name small   # second call
cache:              hit
build time:         0.000s
points:             9
ordered pairs:      72
asymmetric pairs:   35 (97.2%)
triangle violations: 0
```

Incremental vs. full rebuild, N=20→21 (`medium` instance):

| | Time |
|---|---|
| Full rebuild (21 points from scratch) | 13.891s |
| Incremental `add_point` (1 new point onto 20) | 0.956s |
| Speedup | 14.5x |

## Known limitations

- The matrix cache is unbounded — nothing currently prunes
  `data/cache/matrix/`. Not a problem at this project's scale (instances
  are ≤50 points; each cached matrix is at most a few MB), but worth
  noting for a long-running deployment.
- `recompute_on` is built and unit-tested (against the *same* graph, as a
  smoke test — see `tests/test_matrix.py`) but not yet exercised against a
  genuinely different (disrupted) graph, since that graph type doesn't
  exist until Stage 5. Its real proving ground is Stage 6's `T2`/`T3`
  computation.
- `asymmetry_rate` and `triangle_violations` are computed in `O(N²)` and
  `O(N³)` respectively over the *matrix's own points* (not the underlying
  graph), which is trivial at `N<=50` (at most 2,500 and 125,000
  operations) and was not a measurable fraction of any build time observed
  in this stage.

## Next

Stage 4 depends on:
- `Matrix.get_cost`/`get_path` as the `O(1)` primitive the solver's
  nearest-neighbour construction and 2-opt improvement will call
  repeatedly, instead of ever calling Dijkstra directly.
- `build_matrix`'s cache, so re-running a solve on the same instance
  doesn't re-pay the matrix-build cost.
- The asymmetry finding (>95% on real instances) as the concrete reason
  Stage 4's 2-opt must be implemented asymmetry-aware, per the brief.

Stage 4 will build the first real route and the first `T1` number: greedy
nearest-neighbour construction plus 2-opt improvement over this stage's
matrix.
