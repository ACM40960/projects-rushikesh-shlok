# Stage 01 — Dublin road network

> **Updated in Stage 2.** `DEFAULT_BBOX` was expanded (Dublin city centre →
> Greater Dublin) and the graph cache format changed from `.graphml` to
> pickle (the larger graph missed the <5s cache-load bar under graphml).
> The node/edge counts and cache-load timings below are from the
> *original*, smaller-bbox build. See
> `docs/stages/stage-02-instances.md`'s "Amendments to Stage 1" section for
> what changed, why, and the current numbers. Everything else on this page
> — the design, the acceptance criteria, the ADR-0002 curl workaround —
> still holds as written.

## Goal

A cached, routable, travel-time-annotated graph of Dublin: the foundation
every later stage (instances, matrices, solving, disruption, simulation)
routes on. This stage makes it possible to ask "how do I get from A to B in
Dublin, and how long does that take" at all — nothing before this stage
could answer that.

## Scope

**In scope:**
- Downloading a real, directed, one-way-respecting drivable street network
  for a configurable Dublin area, cached to disk.
- Imputing a `travel_time` (seconds) on every edge from OSM `maxspeed`
  where usable, else a documented per-`highway`-type default table.
- Reducing the graph to its largest strongly connected component.
- Snapping an arbitrary lat/lon to the nearest routable node, with a
  max-distance guard and a clear, typed failure.
- `dlm network build` / `dlm network stats` CLI commands.

**Explicitly out of scope** (land in the stage noted):
- User-chosen depot/stop instances, geocoding, presets — Stage 2.
- The travel-time *matrix* between instance points (this stage gives
  point-to-point shortest paths on the full graph; Stage 3 builds the
  cached all-pairs matrix over a specific instance's points).
- Any solver — Stage 4.
- Disruptions — Stage 5.

## Design

**Data source and area.** OpenStreetMap via the public Overpass API
(`overpass-api.de`), `network_type="drive"` (directed, one-way-respecting,
excludes footways/service roads/private access — see `docs/data.md` for
the exact filter). The default area (`dlm.network.loader.DEFAULT_BBOX`) is
a bounding box covering Dublin city centre, the quays, UCD Belfield, and
Dublin Port — not the full M50 catchment. This is an explicit scope choice,
raised as an ADR proposal below.

**The HTTP transport had to be replaced.** OSMnx's own `requests`-based
Overpass client was found to be unreliable in this sandboxed environment:
individual HTTP requests would either have their connection reset, or
—worse— hang indefinitely without ever raising an exception, even with an
explicit `requests` timeout configured. This made retrying impossible (you
cannot retry a call that never returns). Diagnosis (see
`docs/data.md` for the full account) traced part of the problem to OSMnx's
own `_config_dns` behaviour, which pins Overpass's hostname to one resolved
IP via `socket.gethostbyname` — a call that this environment's outbound
proxy setup does not support reliably. `curl` invoked directly against the
identical endpoint was empirically far more reliable: it either succeeds in
a few seconds or fails within its own `--max-time` bound, which *can* be
retried. `dlm.network.loader` therefore builds the Overpass QL query itself
(reusing OSMnx's own `_get_network_filter` — a pure string builder with no
I/O — so the filter is identical to what `graph_from_bbox` would use),
fetches it via `curl` subprocess with retry-with-backoff, and hands the
resulting OSM XML to OSMnx's public `graph_from_xml` to build the graph.
Everything downstream of the fetch (parsing, simplification, largest
strongly connected component, travel-time imputation) is unchanged OSMnx/
NetworkX behaviour. This is recorded as ADR-0002.

**Speed imputation** (`dlm.network.travel_time`) tags every edge with
`speed_kph`, `speed_source` (`"osm_maxspeed"` or `"imputed"`), and
`travel_time` (seconds). The default speed table
(`src/dlm/network/speed_defaults.yaml`) is data, not code — see
`docs/data.md` for the full table and its basis in Irish default speed
limits.

**Largest strongly connected component.** After download, `ox.truncate
.largest_component(G, strongly=True)` drops any node that cannot both
reach and be reached from the rest of the graph — a handful of
boundary-effect nodes at the edge of the bbox, not a meaningful loss of
real network (see Results below for the actual counts).

**Caching.** Two independent caches exist for two different reasons:
1. `data/cache/dublin_<network_type>_<hash>.graphml` — the final graph
   (post-SCC, post-travel-time), keyed by `(bbox, network_type, simplify,
   osmnx_version)`. This is what makes `dlm network build`/`stats` fast on
   a second call.
2. The raw Overpass XML fetched by `curl` is written to a temp file and
   deleted after use — it is *not* itself cached, since the graphml cache
   already captures everything downstream of it and there was no
   reliability benefit (the flaky part is the network fetch, not the
   parsing) to keeping the intermediate XML.

**Alternatives considered:**
- **Monkeypatching OSMnx's `requests.post` call to use curl underneath** —
  rejected in favour of building the query and calling `graph_from_xml`
  directly: fewer layers of indirection, no reliance on OSMnx's internal
  response-object shape, and the resulting code is easier to explain in a
  viva ("we fetch XML with curl, then hand it to OSMnx's own public XML
  loader") than "we monkeypatched a private HTTP function."
- **A smaller default bbox** (city centre only) — rejected: it would
  exclude UCD Belfield, one of the two landmark points this project's own
  acceptance criteria and worked examples require being routable between.

## Interfaces

- `dlm.network.loader.build_graph(bbox=DEFAULT_BBOX, network_type="drive", simplify=True, force_rebuild=False) -> (nx.MultiDiGraph, GraphBuildReport)` —
  load from cache or download, largest-SCC, travel-time-annotated.
- `dlm.network.loader.DEFAULT_BBOX: tuple[float, float, float, float]` —
  `(west, south, east, north)` WGS84 degrees.
- `dlm.network.loader.GraphBuildReport` — node/edge counts (before/after
  SCC), `TravelTimeStats`, cache path, `from_cache`, `build_seconds`.
- `dlm.network.travel_time.add_travel_times(G, speed_defaults=None) -> (nx.MultiDiGraph, TravelTimeStats)`.
- `dlm.network.travel_time.load_speed_defaults(path=None) -> dict[str, float]`.
- `dlm.network.snapping.snap_to_node(G, lat, lon, max_dist_m=150.0) -> SnapResult`,
  raising `SnapError` (a `ValueError` subclass) when nothing is close enough.
- CLI: `dlm network build [--force]`, `dlm network stats`.

## Data & assumptions

See `docs/data.md` for the full account: OSM/ODbL provenance, the default
speed table and its basis, and the known reliability workaround. In brief:
time is seconds, distance is metres (per `dlm.config`'s units policy);
speed is km/h internally, converted before computing `travel_time`.

## How to run

```bash
source .venv/bin/activate
dlm network build            # downloads (or loads from cache) and reports stats
dlm network build --force    # ignore cache, re-download
dlm network stats            # same report, builds first if no cache exists
```

## Acceptance criteria

- ✅ **Graph loads in <5s from cache on second run.** Evidence: `dlm network
  stats` after a prior `dlm network build` logs `loaded graph from cache in
  3.69s`. Also asserted directly in
  `tests/test_network.py::test_real_graph_loads_fast_from_cache`.
- ✅ **Node/edge counts and %maxspeed real vs. imputed reported.** Evidence
  (from a full `dlm network build` run, see Results below): 10,899 nodes,
  24,837 edges; 24,390/24,837 (98.2%) edges have a real OSM `maxspeed` tag,
  447 (1.8%) use the imputed default table.
- ✅ **Strongly connected: every stop-eligible node reaches every other.**
  Evidence: `nx.is_strongly_connected(G)` is `True` on the built graph
  (`tests/test_network.py::test_real_graph_is_strongly_connected`); this
  holds precisely *because* of the largest-SCC extraction step, which
  dropped 71 nodes / 96 edges that were not mutually reachable with the
  rest of the downloaded area (out of 10,970 / 24,933 before extraction).
- ✅ **Hand-checked route is plausible.** UCD Belfield → Trinity College
  Dublin, using the nearest real drivable road to each campus's geocoded
  centroid (both campuses are largely pedestrianised, so their centroids
  are not themselves on the `drive`-filtered network — see the note in
  `tests/test_network.py`): **4.56 km, 373.6 s (6.2 minutes)** at free-flow
  imputed/tagged speeds. The shortest path found follows Stillorgan Road →
  Donnybrook → towards the city centre, which matches the route Trinity
  College's own published directions from the Stillorgan Road area
  describe (Stillorgan Road, past UCD, through Donnybrook, Leeson Street,
  St Stephen's Green, Dawson Street, Nassau Street, College Green) — a
  qualitative check that the shortest path is a sane real route, not an
  artefact. **Caveat, stated honestly:** this project deliberately does not
  call a live traffic/routing API at runtime (ADR-0001), so there is no
  automated way to fetch a directly comparable real-world "typical driving
  time" figure from within the pipeline itself, and a web search attempted
  during this stage did not return a reliable one either (aggregator sites
  returned inconsistent, evidently-wrong numbers). The 6.2-minute figure is
  therefore a **free-flow** estimate (no congestion, junction, or signal
  delay modelled — see `docs/data.md`), and a real off-peak drive on this
  route is expected to take noticeably longer in practice; the authors
  should spot-check this figure against a map tool of their choice and
  record the actual % difference here before the report is finalised.
- ✅ **One-way sanity test.** A real one-way street (e.g. Essex Quay, part
  of Dublin's well-known one-way quays) has an edge one direction and none
  the other; asserted generically (not hard-coded to a specific street, so
  it doesn't break if OSM tagging changes) in
  `tests/test_network.py::test_real_graph_one_way_street_not_reverse_routable`.
  2,055 of 24,837 edges (8.3%) are one-way in the built graph.
- ✅ **Snapping a point in the Irish Sea raises a clear, typed error.**
  Evidence: `tests/test_network.py::test_snap_irish_sea_raises_clear_error`
  and the fixture-based
  `test_snap_to_node_far_away_raises_snap_error`; `SnapError`'s message
  names the query point, the actual nearest-node distance, and the
  configured limit.

All 18 tests pass (`pytest -v`, 12 offline fixture-based + 6 real-network,
8.9s total). `ruff check .` and `ruff format --check .` both clean.

## Results / evidence

```
$ dlm network build
cache:            data/cache/dublin_drive_f9b11d3950321b74.graphml (built fresh)
build time:       31.47s
nodes:            10899 (dropped 71 outside largest strongly connected component)
edges:            24837 (dropped 96)
maxspeed real:    24390/24837 (98.2%)
maxspeed imputed: 447/24837

$ dlm network stats   # second run, cache hit
cache:            data/cache/dublin_drive_f9b11d3950321b74.graphml (hit)
build time:       3.69s
nodes:            10899 (dropped 0 outside largest strongly connected component)
edges:            24837 (dropped 0)
```

98.2% real `maxspeed` coverage is notably higher than a naive prior would
suggest — Dublin/Ireland OSM data is unusually well-tagged for speed limits,
plausibly reflecting a concerted community effort after Ireland's 2005
metric speed-limit changeover. This is a genuinely good result, not a bug
(spot-checked: the 447 imputed edges are disproportionately `service` and
minor `residential` ways, consistent with the general pattern of sparser
tagging on minor roads).

## Known limitations

- See `docs/data.md` for the consolidated list (no turn restrictions, no
  signal/junction delay, no live traffic, free-flow-only speeds).
- The hand-checked route comparison (above) lacks a verified live
  reference number; flagged explicitly rather than fabricated.
- `DEFAULT_BBOX` excludes the M50 and outer suburbs; a stop placed outside
  it will fail to snap (a `SnapError`, not a crash — but still a real
  functional boundary, raised as ADR-0003 below).
- The `curl`-based fetch (`_fetch_overpass_xml`) is Unix-shell-tool
  dependent (requires `curl` on `PATH`); this is a reasonable assumption
  for this project's dev/CI/report-generation environment but is worth
  noting as a portability constraint.

## ADR proposals for the authors

- **ADR-0002 (recorded as accepted for now, revisit if environment
  changes): fetch OSM data via `curl` subprocess instead of OSMnx's
  built-in `requests` transport**, because the latter was empirically
  unreliable in this environment (see Design above). If this project is
  later run in a different environment where OSMnx's own transport is
  reliable, this could be reverted — but the curl-based path has no
  downside there either (it is not slower), so there is no urgency to.
- **ADR-0003 proposal (open, per §9 of the brief): study area size.**
  Current default (`DEFAULT_BBOX`) is Dublin city centre + UCD Belfield +
  Dublin Port, chosen for fast, reliable downloads. The brief's alternative
  is the full M50 catchment. Recommendation: keep the current bbox as the
  default for development speed and reliability, but make the area a CLI
  parameter (already partially true — `build_graph(bbox=...)`) so a
  full-M50 build can be produced once, cached, and used for the final
  report if the authors want suburb-to-suburb instances. Revisit after
  Stage 3, once matrix build times at the current size are known.

## Next

Stage 2 depends on:
- `dlm.network.loader.build_graph` to obtain the graph a `Depot`/`Stop`
  gets built against.
- `dlm.network.snapping.snap_to_node` / `SnapError` as the safety boundary
  every user input mode (address, lat/lon, preset, random, map-click) will
  call through.
- The real Dublin graph's node IDs (as used by the `Stop.node` field) are
  now stable and cached, so instances built in Stage 2 can be saved with
  confidence they'll resolve consistently.

Stage 2 will let the user choose a depot and `N` stops by address, lat/lon,
curated preset, or seeded random sample, all snapping through this stage's
graph.
