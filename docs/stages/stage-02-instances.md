# Stage 02 — Dynamic delivery instances

## Goal

Let the user decide **how many** stops and **which** stops — from the
command line, reproducibly, with full add/remove/move support — and make
that instance saveable, loadable, and visually checkable. This is where
the project's dynamic-`N` requirement (brief §1.1) actually lives in code:
after this stage, nothing downstream may assume a fixed number of stops.

## Scope

**In scope:**
- `Stop`/`Depot`/`Instance` schema with `n_stops` as a derived property.
- `InstanceBuilder`: add/remove/move/rename, four input modes (address,
  lat/lon, preset, seeded random), save/load, `build()` validation.
- ~30 curated, real, geocoded Dublin location presets.
- Cached Nominatim geocoding with ambiguity detection.
- `dlm instance new/add/remove/move/rename/random/list/show/map` CLI.
- Three canonical, named-location instances: `small` (N=8), `medium`
  (N=20), `large` (N=40).

**Explicitly out of scope** (land in the stage noted):
- The travel-time *matrix* between instance points — Stage 3.
- Map-click input — Stage 10 (its `add_stop(lat, lon)` API is this
  stage's `add_stop_from_latlon`, already built; Stage 10 only adds the
  click-to-coordinate UI wiring).
- Any solver — Stage 4.

## Design

**`N` is a property, never a stored constant.** `Instance.n_stops` is
`len(self.stops)`, computed on every access. No cache key, matrix
dimension, test parametrisation, or CLI command anywhere assumes a
particular `N` — the parametrised test suite (`N` = 1, 2, 3, 8, 20, 40)
exists specifically to catch a regression here.

**`Instance` is not `build()`-validated at construction.** The brief asks
for both "an instance is saveable/loadable at any point while building it
interactively" *and* "1 ≤ N ≤ 50, validated on `build()`." Those two
requirements are in tension if validation runs at every mutation: right
after `dlm instance new` sets only a depot, the instance has zero stops,
and that must be a valid thing to have *saved to disk* (so a later
`dlm instance add` can load it back). The resolution: `Instance` (the
pydantic model) accepts any structurally valid data, including zero
stops — it is also literally the JSON schema persisted to
`data/instances/<name>.json`, whether "finished" or mid-edit, so there is
only one file format, not a separate draft/final split. `InstanceBuilder.build()`
is the one place the business rules (depot set, `1 <= N <= 50`, no
depot/stop node collision, no reference to a node the current graph no
longer has) are enforced, raising `InstanceValidationError` with *every*
problem found, not just the first. `dlm instance show` calls `build()` and
reports pass/fail as a `status:` line rather than crashing, so an
in-progress instance stays inspectable.

**Stop ids are stable, not positional.** Each stop gets `s{n}` from a
monotonically increasing counter that never reuses a number within a
builder session (reconstructed from existing ids on load, so it continues
correctly across separate CLI invocations). This means `dlm instance
remove --stop s3` keeps meaning the same stop across a session, but it
also means **add → remove → add does not reproduce the same id** a direct
build would assign (see the acceptance criteria below for what "identical"
means here instead, and why).

**Presets are geocoded, not guessed** — all ~30 were resolved via
`dlm.instance.geocode` against real Nominatim queries during this stage's
development (see `data/presets/dublin_locations.yaml`'s header), not typed
in from memory. Six needed a refinement: a handful of large-area places
(UCD Belfield, Dublin Airport, Phoenix Park, St Vincent's Hospital, Trinity
College, Liffey Valley Shopping Centre) have geocoded centroids that are
farther than the snap-distance guard from any drivable road — reasonable,
since a hospital campus or an airport's actual "point" is deep inside a
mostly pedestrian/restricted area. Those six use the nearest real drivable
access point instead (found by nearest-node search, not guessed), which is
also the more *correct* choice for a delivery routing project: a van
cannot drive to a geometric centroid.

**The bbox had to grow.** Several category-appropriate presets (Dublin
Airport, Tallaght, Blanchardstown, Dun Laoghaire, Swords) fell outside
Stage 1's original, smaller bounding box. Resolved as ADR-0003: the
default download area (`dlm.network.loader.DEFAULT_BBOX`) now covers
Greater Dublin — city centre, UCD, Dublin Port, the airport, and the outer
suburbs. See "Amendments to Stage 1" below for what this changed and why
it's recorded here rather than silently.

**Geocoding goes through `curl`, like Stage 1's Overpass fetch.** Same
underlying environment reliability reason (ADR-0002) — reusing the
established pattern rather than re-deriving a new one for a second
external API.

**Ambiguity detection.** A query is "ambiguous" if a second candidate is
both far from the top result (>500m — filters out near-duplicate address
variants of the same real place) *and* comparably important (importance
≥ 50% of the top result's — filters out an obviously-dominant match with
a long tail of unrelated same-named places). Two real, comparably
significant places sharing a name (e.g. "Main Street", which exists in
many separate Irish towns) triggers `AmbiguousGeocodeError` with
candidates; a dominant match with minor noise below it does not.

**Alternatives considered:**
- **Renumbering stop ids to list position at `build()` time** (so
  add→remove→add would produce byte-identical ids to a direct build) —
  rejected: would make `show`/`map`'s ids change invisibly between calls
  to `build()`, which is more confusing than comparing instances by
  content instead (see the test's docstring for the reasoning).
- **A separate `InstanceDraft` class distinct from `Instance`** — rejected
  as unnecessary complexity once it was clear a single schema with
  deferred validation covers both the "saveable mid-edit" and "validated
  before use" needs.

## Interfaces

- `dlm.instance.schema`: `Stop`, `Depot(Stop)`, `Instance`,
  `InstanceValidationError`, `StopSource` enum (`address`/`latlon`/
  `preset`/`random`/`map_click`). `Instance.n_stops -> int` (property).
- `dlm.instance.builder.InstanceBuilder(graph, name, seed=None, fleet_size=1, vehicle_capacity=None)`:
  `set_depot_from_{address,latlon,preset}`, `add_stop_from_{address,latlon,preset}`,
  `add_random_stops(n, seed) -> list[MutationResult]`, `move_stop`,
  `remove_stop`, `rename_stop`, `save(path)`, `InstanceBuilder.load(graph, path)`
  (classmethod), `build() -> Instance`. Every mutator returns a `MutationResult`
  (`.action`, `.message`, `.stop`).
- `dlm.instance.geocode.geocode(query, country_codes="ie", limit=5) -> GeocodeResult`,
  raising `GeocodeError` / `AmbiguousGeocodeError` (`.candidates`).
- `dlm.instance.presets.get_preset(name) -> Preset`, `load_presets() -> list[Preset]`,
  raising `PresetNotFoundError`.
- `dlm.viz.folium_map.render_instance_map(instance) -> folium.Map`,
  `save_instance_map(instance, path) -> Path`.
- CLI: `dlm instance new|add|remove|move|rename|random|list|show|map`.

## Data & assumptions

- Snap-distance guard: default 150m (`dlm.network.snapping.DEFAULT_MAX_SNAP_DIST_M`,
  unchanged from Stage 1), applied on every add/move/set-depot call.
- `1 <= N <= 50` (`dlm.instance.schema.MIN_STOPS`/`MAX_STOPS`).
- Random stops: sampled via `random.Random(seed).shuffle()` over
  `list(graph.nodes)`, skipping nodes already used by the depot or other
  stops — deterministic given `(graph, seed, n)`.
- Geocoding is biased to `countrycodes=ie` throughout.
- Ambiguity thresholds: >500m apart and ≥50% relative importance (see Design).

## How to run

```bash
source .venv/bin/activate

# Build an instance interactively, one command per mutation:
dlm instance new --name demo --depot-preset "Connolly Station"
dlm instance add --name demo --preset "Trinity College Dublin"
dlm instance add --name demo --address "Grafton Street, Dublin"
dlm instance add --name demo --latlon 53.3382,-6.2591 --label "Rathmines pt"
dlm instance random --name demo --n 5 --seed 42

dlm instance list
dlm instance show --name demo
dlm instance move --name demo --stop s2 --latlon 53.3265,-6.2649
dlm instance rename --name demo --stop s2 --label "Rathmines Corner"
dlm instance remove --name demo --stop s5
dlm instance map --name demo   # writes results/instance_maps/demo.html
```

## Acceptance criteria

- ✅ **N = 1, 2, 3, 8, 20, 40 all work, no hard-coded size assumption.**
  `tests/test_instance.py::test_instance_builds_and_round_trips_for_various_n`,
  parametrised over exactly those six sizes (N=1 and N=2 included
  explicitly, per the brief). Build, validate, save, and load all pass for
  every size in the same test.
- ✅ **Add → remove → add == direct construction.**
  `test_add_remove_add_equals_direct_construction`. Compares stop
  *content* (label/lat/lon/node/source), not ids — see the Design section
  above for why id equality is the wrong notion of "identical" here, and
  the test's own docstring for the same reasoning inline with the code.
- ✅ **Round-trip save→load is lossless.** `test_round_trip_save_load_is_lossless`:
  asserts both Python equality and identical JSON serialisation after a
  save/load cycle including a rename.
- ✅ **Address / preset / lat-lon of the same place resolve to the same
  node.** `test_same_place_via_preset_latlon_and_address_resolves_same_node`,
  using "Grafton Street" (a preset whose curated coordinate is exactly its
  own geocode result, so re-geocoding it at test time reproduces the same
  point — see the test's docstring for why the six *refined* presets,
  e.g. Trinity College, aren't used for this specific test).
- ✅ **A stop in the Irish Sea fails with a readable error.**
  `test_stop_in_irish_sea_raises_readable_error_not_a_stack_trace` (and the
  equivalent Stage 1 test) — `SnapError`, not a stack trace.
- ✅ **An ambiguous address returns candidates.**
  `test_ambiguous_address_returns_candidates_not_a_guess`, querying "Main
  Street" (matches multiple distinct real Irish towns).
- ✅ **`dlm instance map` output visually shows the chosen stops in the
  right places (screenshot committed).** `docs/report/instance_map_small.png` —
  see below for how it was produced and why that needed its own
  workaround.

All 39 tests pass (18 offline + 21 for this stage, 8 offline / 13 network-marked
within it); `ruff check .` / `ruff format --check .` clean.

## Results / evidence

```
$ dlm instance list
large: 40 stops, depot=Dublin Port
medium: 20 stops, depot=Dublin Port
small: 8 stops, depot=Dublin Port
```

All three canonical instances are built entirely from named locations
(presets + real geocoded Dublin addresses) — `test_canonical_instances_load_and_validate`
asserts no stop's `source` is `"random"`. `large` combines all 30 presets
with 10 additional named Dublin neighbourhoods/streets to reach N=40.

**The screenshot** (`docs/report/instance_map_small.png`) required its own
workaround, on top of ADR-0002's: headless Chromium's own networking
cannot reach external CDNs in this sandbox (confirmed not a proxy
configuration issue — `curl` reaches every relevant host, e.g.
`cdnjs.cloudflare.com`, fine; Chromium gets `net::ERR_CONNECTION_RESET`
even pointed explicitly at the same working proxy). Rather than give up on
a real rendered screenshot, `experiments/render_map_screenshot.py`
intercepts every non-`file://` request Chromium makes and fetches it via
`curl` on Chromium's behalf, cached to disk — the same "curl is reliable
here, this environment's other HTTP stacks are not" pattern as ADR-0002,
applied one layer up. The result is a genuine rendered map: real OSM
basemap tiles, the depot as a black home-icon marker, stops as blue
info-icon markers, all in their correct relative positions. This script is
kept in `experiments/` (not `src/dlm/`) since it exists only to work
around this specific sandbox's browser networking for documentation
screenshots — on a normal machine, the plain HTML file just renders.

## Known limitations

- Ambiguity detection (>500m + ≥50% relative importance) is a heuristic,
  not a guarantee — a pathological case could still slip through as
  falsely unambiguous, or flag two really-the-same-place results (seen
  during preset curation for a few Dublin suburbs with multiple ward-
  boundary-based Nominatim entries; worked around by picking a differently
  phrased query for those specific presets rather than by loosening the
  heuristic).
- `InstanceBuilder.build()`'s reachability guarantee is structural (every
  node comes from Stage 1's largest strongly connected component), not
  re-verified pairwise per instance — see the comment in `builder.py`.
  This stops being true once a *disrupted* graph view exists (Stage 5),
  which is exactly why Stage 5 owns its own first-class connectivity
  check rather than this method being extended to cover it.
- The stop-stop "same node" check is a warning, not an error (per the
  brief); there is no interactive merge flow — a user must remove one
  manually.
- The screenshot workaround (`experiments/render_map_screenshot.py`) is
  specific to this sandboxed environment; it is not needed, and is not
  invoked by, any part of the actual pipeline or its automated tests.

## Amendments to Stage 1

Two changes were made to already-"complete" Stage 1 code, both driven by
concrete needs surfaced while curating this stage's presets, and both
recorded rather than made silently:

- **`DEFAULT_BBOX` expanded** (ADR-0003, referenced above) from Dublin
  city centre to Greater Dublin, once several category-appropriate
  presets (the airport, outer suburbs) turned out to fall outside the
  original area.
- **Graph cache format changed from `.graphml` to pickle.** The larger
  graph (28,112 nodes after the bbox change, up from 10,899) took
  **10.42s** to parse from `.graphml` on a cache hit — past Stage 1's own
  <5s acceptance bar. Pickle loads the identical graph in **0.44–1.3s**.
  `docs/stages/stage-01-network.md` and `docs/data.md` describe the
  original (smaller-bbox, graphml-cached) measurements from when Stage 1
  was written; the numbers below are current.

Current `dlm network build`/`stats` numbers (Greater Dublin bbox, pickle cache):

```
$ dlm network build
nodes:            28112 (dropped 81 outside largest strongly connected component)
edges:            62068 (dropped 107)
maxspeed real:    55750/62068 (89.8%)
maxspeed imputed: 6318/62068

$ dlm network stats   # second run, cache hit
build time:       1.11s
```

## ADR proposals for the authors

- **ADR-0003 (resolved this stage, as above):** default download area is
  now Greater Dublin. Still short of the full M50 catchment — revisit
  again after Stage 3's matrix build times are known, per the original
  Stage 1 proposal.
- **Default depot** (open, per §9 of the brief): should there be a
  sensible default named Dublin depot location, or must the user always
  choose explicitly? This stage's canonical instances use "Dublin Port"
  (a real, plausible logistics depot) but `dlm instance new` still
  requires an explicit `--depot-*` flag — no code-level default exists
  yet. Recommendation: keep requiring an explicit choice (matches "the
  depot is just a special stop, chosen the same way" from the brief) but
  document "Dublin Port" as the report's own convention.
- **Which locations belong in the curated preset list** (open, per §9):
  the current 30 (`data/presets/dublin_locations.yaml`) span hospital /
  university / retail / suburb / transport_hub / landmark, chosen for
  name-recognisability and geographic spread rather than any formal
  selection criterion. Worth a quick sanity pass from the authors, who
  know Dublin better than a geocoding script does.

## Next

Stage 3 depends on:
- `Instance`/`Stop`/`Depot` and their `.node` fields as the set of points
  the travel-time matrix is built over.
- `InstanceBuilder`'s incremental mutation model (add/remove/move) as the
  contract the matrix's own incremental update API (`add_point`/
  `remove_point`/`move_point`) must stay in sync with — adding one stop to
  an instance should cost one new matrix row/column, not a full rebuild.
- The three canonical instances (`small`/`medium`/`large`) as the fixtures
  Stage 3's own timing/acceptance evidence will be measured against.

Stage 3 will build the cached, incrementally-updatable travel-time +
path matrix over an instance's points.
