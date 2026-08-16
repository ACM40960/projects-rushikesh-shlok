# Data

## Source

The road network comes from [OpenStreetMap](https://www.openstreetmap.org)
(OSM), downloaded via [OSMnx](https://osmnx.readthedocs.io) against the
public [Overpass API](https://overpass-api.de) (`overpass-api.de`). OSM data
is © OpenStreetMap contributors and is licensed under the
[Open Database License (ODbL) 1.0](https://opendatacommons.org/licenses/odbl/1.0/).
Any distribution of this project's derived graph data must carry the same
attribution: "© OpenStreetMap contributors" (see the
[ODbL summary](https://www.openstreetmap.org/copyright) for what that
requires in practice — attribution and share-alike on the data, not on
software built with it).

Geocoding (from Stage 2 onward) uses OSM's
[Nominatim](https://nominatim.openstreetmap.org) service, under the same
licence and its own [usage policy](https://operations.osmfoundation.org/policies/nominatim/).

## Area and download

The default download area is a bounding box covering Dublin city centre,
the north and south quays, and out to UCD Belfield in the south-east and
Dublin Port in the east — see `dlm.network.loader.DEFAULT_BBOX` — rather
than the full M50 catchment. This was a deliberate scope choice for this
stage (see the ADR proposal in `docs/stages/stage-01-network.md`): it keeps
downloads fast and reliable while covering every landmark location used in
this project's worked examples and acceptance tests.

Network type is OSMnx's `"drive"` filter: standard car-accessible roads,
directed and one-way-respecting. The graph is reduced to its **largest
strongly connected component** after download, so any node reachable from
the depot can also reach it back — a handful of nodes on the bbox's edge
that only connect one-way to the rest of the downloaded area are dropped
(see the build report's `dropped nodes/edges` counts in
`docs/stages/stage-01-network.md` for the actual figures).

## Known reliability issue: the public Overpass instance

`overpass-api.de` is shared community infrastructure, not a dedicated
resource for this project, and its connection behaviour was unreliable
during development in this environment: individual HTTP requests
(especially OSMnx's own pre-flight `/api/status` server-load check) would
intermittently reset rather than complete. Two changes address this in
`dlm.network.loader`:

- **`overpass_rate_limit = False`** — skips OSMnx's `/api/status`
  pre-check (which polls before every query to see if a slot is free);
  that specific endpoint reset far more often than the actual data
  endpoint (`/api/interpreter`) did, so skipping it removes the least
  reliable part of the round trip without removing any real functionality.
- **Retry with backoff** (`_download_with_retries`, up to 10 attempts,
  2s apart) — a connection reset on the data endpoint itself was also
  observed intermittently (consistent with the public instance being
  under load from many concurrent users), so the loader retries rather
  than failing on the first reset.

This is recorded as a known limitation, not silently hidden: a from-scratch
`dlm network build` can take longer than the request itself would suggest,
because of these retries. Once downloaded, the result is cached to
`data/cache/*.graphml` (see `dlm.network.loader._cache_path`), so this cost
is paid once per (area, network type, simplify, OSMnx version) combination,
not on every run.

## Speed table (imputed travel times)

Where an OSM way has no usable `maxspeed` tag, `dlm.network.travel_time`
imputes a speed from a per-`highway`-type default table
(`src/dlm/network/speed_defaults.yaml`), in km/h:

| `highway` | Default speed (km/h) | Basis |
|---|---|---|
| `motorway` | 120 | Irish motorway default limit |
| `motorway_link` | 80 | Slip road, well below the default |
| `trunk` | 100 | Irish national road (N-road) default limit |
| `trunk_link` | 60 | Slip road |
| `primary` | 80 | Irish regional road (R-road) default limit |
| `primary_link` | 50 | Slip/junction road |
| `secondary` | 80 | Regional road default |
| `secondary_link` | 50 | Slip/junction road |
| `tertiary` | 60 | Local road, sub-regional-road default |
| `tertiary_link` | 50 | Slip/junction road |
| `unclassified` | 50 | Irish "urban"/built-up default limit |
| `residential` | 50 | Irish "urban"/built-up default limit |
| `living_street` | 20 | Shared surface, well below the legal default |
| `service` | 20 | Yards, car parks, access roads |
| `track` | 30 | Unpaved/agricultural access |
| `road` | 50 | Unknown classification — urban default as a safe fallback |
| *(fallback)* `default` | 50 | Any `highway` value not listed above |

Basis: the [Road Traffic Act 2004 (Ireland) default speed limits](https://www.rsa.ie)
— 50 km/h in "urban"/built-up areas, 80 km/h on regional and local roads
outside built-up areas, 100 km/h on national roads, 120 km/h on
motorways — adjusted downward for classes (`service`, `living_street`,
`track`) where the statutory default is rarely the realistic free-flow
speed. These are **free-flow** defaults, not observed traffic speeds: see
Limitations below.

`dlm.network.travel_time.add_travel_times` tags every edge with
`speed_source` (`"osm_maxspeed"` or `"imputed"`) so the real-vs-imputed
split is always inspectable, and reports the split as a
`TravelTimeStats` (see `dlm network build`/`stats` output and
`docs/stages/stage-01-network.md` for the actual measured percentage on
the Dublin graph).

## Known limitations

- **No turn restrictions.** OSM turn-restriction relations are not applied;
  a shortest path may include a turn that is illegal in reality (e.g. a
  banned right turn).
- **No traffic-signal or junction delay.** Travel time is pure
  distance/speed; junctions, signals, and give-way delay are not modelled.
- **No live traffic.** Speeds are free-flow defaults or OSM's static
  `maxspeed` tag, not real-time congestion — this is a deliberate
  reproducibility choice (ADR-0001), not an oversight.
- **`maxspeed` coverage is incomplete and inconsistent in OSM,** as in any
  crowdsourced dataset; the imputed default table is a documented modelling
  assumption, not a measurement.
- **One-way and directionality come entirely from OSM's `oneway` tagging**
  as interpreted by OSMnx; any tagging error in OSM propagates here.
- **The default download area excludes the M50 and outer suburbs** (see
  the city-centre-vs-M50 ADR proposal in `docs/stages/stage-01-network.md`);
  a stop placed outside `DEFAULT_BBOX` will fail to snap.
