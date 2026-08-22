# Road-network data

## Coverage

The default bounding box is `(-6.42, 53.28, -6.10, 53.47)`, covering
Greater Dublin, including the city centre and several outer areas such as
Tallaght, Blanchardstown, Dún Laoghaire, Swords and Dublin Airport. It does
not cover the complete Greater Dublin commuter region.

The graph uses OSMnx's `drive` filter and is directed so one-way restrictions
are retained. After download, only the largest strongly connected component is
kept. This guarantees mutual route reachability in the base graph, although a
closure can make a disrupted graph infeasible.

## Download mechanism

The loader constructs an Overpass query using OSMnx's driving-network filter
and sends the request using `curl`. It makes up to eight attempts with a
three-second retry interval. OSMnx then parses and simplifies the returned OSM
XML.

## Cache and reproducibility

Graph caches use `data/cache/*.pkl`. The exact snapshot used for the committed
experiments is:

```text
data/cache/dublin_drive_664cee449591eb29.pkl
```

It contains 28,112 nodes and 62,068 directed edges and is committed so the
reported experiments remain reproducible when OpenStreetMap changes. Its
SHA-256 digest is stored in the adjacent `.sha256` file and verified before
`pickle.load` runs. Pickle must not be used for untrusted data; the application
does not accept uploaded pickle files.

New graph builds store pre/post strongly connected-component counts in a
metadata sidecar. This information was not stored with the older committed
snapshot, so a cache hit reports the pre-SCC counts as unavailable rather than
claiming that zero nodes or edges were dropped.

## Travel-time attributes

When OSM contains a usable `maxspeed`, it is parsed and used. Otherwise the
model assigns a fallback speed from `src/dlm/network/speed_defaults.yaml` based
on the OSM `highway` tag. Edge travel time is

\[
t_e = \frac{\text{length}_e}{\text{speed}_e}.
\]

These values are modelling assumptions used when OSM has no usable `maxspeed`.
They are not a claim that every OSM road with a given `highway` tag has that
legal speed limit.

Many Irish rural local-road speed limits changed from 80 km/h to 60 km/h on
7 February 2025. See the [Department of Transport notice](https://www.gov.ie/en/department-of-transport/press-releases/speed-limits-on-rural-and-local-roads-change-from-80kmh-to-60kmh/).
The fallback table should therefore be interpreted only as a routing-model
assumption, not a legal speed-limit database.

## Data quality

The snapshot inherits omissions, tagging errors and simplifications in
OpenStreetMap. It contains no live congestion, signal timing, queueing,
incident feed or vehicle-specific restrictions beyond the selected OSM drive
network. See [limitations.md](limitations.md) and [../DATA_NOTICE.md](../DATA_NOTICE.md).
