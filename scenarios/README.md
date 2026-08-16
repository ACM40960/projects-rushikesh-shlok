# Scenarios

Version-controlled disruption scenario YAML files: `library/` holds four
curated, real-Dublin scenarios shipped with the project; anything you
author yourself (by hand, or later via the Stage 10 UI) can live directly
in this directory. Both are found the same way — `dlm disrupt list` /
`validate` / `preview` search this directory recursively by filename stem.

The models are `dlm.disruption.schema.Scenario` / `Disruption`. Validate
any file with `dlm disrupt validate --scenario <name>` before relying on
it — it resolves every disruption's geometry against the real Dublin graph
and reports problems (an address nowhere near a road, an unreachable
corridor) without applying anything.

## Schema

A `Scenario` is a name plus a list of `Disruption`s:

```yaml
schema_version: 1
name: my_scenario
description: optional free text
source: optional citation for where this scenario comes from
disruptions:
  - id: unique_within_this_file
    shape: edge | node | corridor | polygon   # WHAT part of the graph
    effect: closure | slow_zone               # WHAT HAPPENS to it
    description: optional free text
    citation: optional source note
    # ... shape-specific geometry fields (see below)
    # ... effect-specific fields (see below)
    time_window: [start_s, end_s]   # optional; omit = active for the whole run
    severity: 0.0-1.0                # optional, default 1.0; informational only
                                      # (read by Stage 7's generators, not by the engine)
```

**`shape`** — what the disruption targets:

| shape | fields | meaning |
|---|---|---|
| `edge` | `from_node`+`to_node` (int node ids) **or** `from_latlon`+`to_latlon` ([lat, lon]); optional `directions: both\|forward\|reverse` (default `both`) | one specific street segment |
| `node` | `node` (int) **or** `at` ([lat, lon]) | a junction — every edge touching it |
| `corridor` | `waypoints` (list of >= 2 `[lat, lon]`) | a route: waypoints are snapped, and every edge on the shortest path between each consecutive pair (on the *undisrupted* graph) is affected — this is what lets 2-6 waypoints describe a whole street's worth of edges |
| `polygon` | `boundary` (list of >= 3 `[lat, lon]`, auto-closed) | an area: every edge with at least one endpoint inside |

Node/corridor/polygon shapes always affect every direction found (a
blocked junction or cordoned street blocks all approaches); only `edge`
supports a directional restriction, since only a single named segment has
a realistic case for "closed only westbound."

**`effect`** — what happens to the edges the shape resolves to:

| effect | fields | meaning |
|---|---|---|
| `closure` | (none extra) | edges are removed entirely — impassable |
| `slow_zone` | `speed_factor` (0 < x < 1) | travel time is divided by `speed_factor` (e.g. `0.5` doubles it) — edges stay usable, just slower |

If a scenario's disruptions overlap on the same edge, **closures always
win over slow zones**, and **the first-listed disruption wins within the
same effect** — see `docs/stages/stage-05-disruptions.md` for why
(compounding overlapping effects would make results order-fragile).

Coordinates are always `[lat, lon]` (WGS84 decimal degrees), matching the
project's convention everywhere else — **not** GeoJSON's `[lon, lat]`.

## Worked example

`library/luas_works_dawson_street.yaml` — the simplest curated scenario, a
`corridor` + `slow_zone`:

```yaml
schema_version: 1
name: luas_works_dawson_street
disruptions:
  - id: dawson_st_works
    shape: corridor
    effect: slow_zone
    waypoints:
      - [53.3382, -6.2591]   # St Stephen's Green, Dawson Street junction
      - [53.3423, -6.2577]   # Dawson Street / Nassau Street junction
    speed_factor: 0.2
    time_window: [0, 604800]
    severity: 0.5
```

Applying it: `dlm.disruption.engine.apply_scenario(graph, scenario)` snaps
both waypoints to graph nodes, walks the shortest path between them,
divides every edge's `travel_time` on that path by `0.2` (5x slower), and
returns a `DisruptionResult` — a *copy* of `graph` with that change, plus
an audit of exactly which edges changed. The original `graph` is
untouched.

## Curated library

`library/` — four real, cited Dublin disruption scenarios, one of each
`shape` (bar `edge`, which has no realistic single-segment curated example
at this scale) crossed with a closure or a slow zone:

| File | shape | effect | Real-world basis |
|---|---|---|---|
| `st_patricks_day_parade.yaml` | corridor | closure | The published St Patrick's Festival parade route |
| `oconnell_street_protest.yaml` | polygon | closure | Recurring O'Connell Street rally/protest closures |
| `luas_works_dawson_street.yaml` | corridor | slow_zone | Track/utility works on the Luas Cross City corridor |
| `liffey_quays_closure.yaml` | corridor | closure | Recurring north-quays incident closures (Bachelors Walk / Eden Quay) |

See each file's `source`/`citation` fields and
`docs/stages/stage-05-disruptions.md` for measured effects (edges
affected, whether the graph stays strongly connected) against the real
Dublin graph.
