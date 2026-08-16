# CLI reference

`dlm` is the only way `app/` (Stage 10) is allowed to reach the pipeline —
see `docs/architecture.md`'s "thin client" law. Every command below is a
real, tested command as of Stage 9; run any of them with `--help` for the
exact option list (this doc is a guide, `--help` is the source of truth).

```bash
dlm --version
dlm --help
```

All commands share two behaviours worth knowing up front:

- **Caching.** `network build`/`instance matrix` (and everything that
  calls them internally) cache to `data/cache/`, keyed by their inputs.
  Re-running the same command again is fast; pass `--force` to bypass
  the cache.
- **Run directories.** Anything that produces a result (`plan`,
  `compare`, `batch`, `sensitivity`, `benchmark`) writes a timestamped
  folder under `results/` (`config.yaml` + `result.json`/CSV +, where
  relevant, a map) — the full, reproducible record of that one
  invocation — in addition to any `--out` copy used by the report.

## `dlm network`

| Command | What it does |
|---|---|
| `dlm network build [--force]` | Download (or load from cache) the Dublin routable graph; report node/edge counts and `maxspeed` imputation stats. |
| `dlm network stats` | Same report, building the graph first if it isn't cached yet. |

```bash
dlm network build
```

## `dlm instance`

Builds and edits a saved instance (`data/instances/<name>.json`): a depot,
zero or more stops, and (Stage 8) a fleet size / vehicle capacity.

| Command | What it does |
|---|---|
| `dlm instance new --name X --depot-address/--depot-latlon/--depot-preset ... [--fleet-size K] [--vehicle-capacity C] [--seed S] [--force]` | Create a new instance. Exactly one `--depot-*` flag is required. |
| `dlm instance add --name X --address/--latlon/--preset ... [--label L] [--demand D]` | Add one stop. Exactly one location flag is required; `--demand` matters only for `fleet-size > 1` (CVRP). |
| `dlm instance random --name X --n N [--seed S]` | Add `N` seeded-random stops drawn from real routable graph nodes. |
| `dlm instance remove --name X --stop ID` | Remove a stop by id (see `dlm instance show`). |
| `dlm instance move --name X --stop ID --latlon 'lat,lon'` | Move an existing stop, re-snapping to the graph. |
| `dlm instance rename --name X --stop ID --label L` | Rename a stop. |
| `dlm instance list` | List all saved instances and their stop counts. |
| `dlm instance show --name X` | Full detail: depot, every stop, and whether the instance currently passes validation. |
| `dlm instance map --name X [--out PATH]` | Render a standalone Folium HTML map of the instance. |
| `dlm instance matrix --name X [--force]` | Build (or load from cache) the travel-time matrix over the instance's depot + stops; report size/asymmetry/triangle-violation stats. |

```bash
dlm instance new --name demo --depot-preset dublin_port
dlm instance add --name demo --preset trinity_college --demand 2
dlm instance random --name demo --n 5 --seed 1
dlm instance show --name demo
```

## `dlm plan`

```bash
dlm plan --instance X [--solver nn_2opt|nearest_neighbour]
```

Solves an instance's baseline route and reports `T1`. `fleet_size == 1`
(the default): a single route via `--solver` (`nn_2opt` or
`nearest_neighbour`). `fleet_size > 1`: multiple vehicle routes via
Clarke-Wright + 2-opt (Stage 8) — `--solver` is ignored in that case.
Writes `results/<instance>-<timestamp>/{config.yaml, result.json,
route_map.html}`.

## `dlm compare`

```bash
dlm compare --instance X --scenario Y [--solver nn_2opt|nearest_neighbour]
```

Computes `T1`/`T2` (both information models)/`T3`/`T3_oracle`/`Saving %`
for one instance under one disruption scenario (see `dlm disrupt list`
for available scenario names). Writes
`results/<instance>-vs-<scenario>-<timestamp>/` with `config.yaml`,
`result.json`, `route_map.html` (the `T1` plan), and
`disruption_map.html` (what the scenario changed).

## `dlm disrupt`

Authors, validates, and previews disruption scenario YAML files under
`scenarios/` (see `scenarios/README.md` for the file format).

| Command | What it does |
|---|---|
| `dlm disrupt list` | List every scenario YAML found under `scenarios/` (recursive), with its disruption count. |
| `dlm disrupt validate --scenario Y` | Resolve every disruption in a scenario against the real graph and report problems, without applying anything. Exits non-zero if invalid. |
| `dlm disrupt preview --scenario Y [--at-time SECONDS] [--out PATH]` | Apply a scenario to the real graph, report the audit (edges closed/slowed, still-strongly-connected), and render a map of the affected edges. |
| `dlm disrupt new --name Y --shape edge\|node\|corridor\|polygon --effect closure\|slow_zone [--force]` | Scaffold a new scenario YAML with placeholder geometry, ready to hand-edit. |

```bash
dlm disrupt list
dlm disrupt new --name my_closure --shape edge --effect closure
# edit scenarios/my_closure.yaml with real coordinates, then:
dlm disrupt validate --scenario my_closure
dlm disrupt preview --scenario my_closure
```

## `dlm batch`

```bash
dlm batch [--instances small,medium,large] [--n-random 10] [--seed 42] \
          [--solver nn_2opt|nearest_neighbour] [--out PATH]
```

Runs `T1`/`T2`/`T3`/`T3_oracle`/`Saving %` across every (instance,
scenario) pair — the curated scenario library plus `--n-random` seeded
random scenarios — and writes an aggregate CSV. Writes
`results/batch-<timestamp>/{config.yaml, batch_results.csv,
summary.json}`, and a copy to `--out` (default
`docs/report/batch_results.csv`, the file `dlm figures` and the report
read from). This is what `make experiment` runs.

## `dlm figures`

```bash
dlm figures [--results PATH] [--instance NAME] [--out DIR]
```

Turns a `dlm batch` results CSV into report-ready PNG+SVG figures. Pure
post-processing (no graph/instance/solver access), so it's fast and safe
to re-run any time the CSV or figure styling changes. Defaults:
`--results docs/report/batch_results.csv`, `--out docs/report/figures/`.
This is what `make figures` runs.

## `dlm sensitivity`

```bash
dlm sensitivity [--instances small,medium,large] [--values 60,120,180,240,300] \
                 [--solver nn_2opt|nearest_neighbour] [--out PATH]
```

Sweeps `default_service_time_s` and reports how sensitive `T1` is — the
concrete check ADR-0004 asked for before its 180s default was treated as
final. Each instance is solved once (the solver never sees service time,
so the route and `drive_time_s` are identical at every value); only
`service_time_s`/`total_time_s` and service time's share of the total
change. Default `--out docs/report/sensitivity_results.csv`.

## `dlm benchmark`

```bash
dlm benchmark [--instances small,medium,large,fleet] [--time-limit 10.0] [--out PATH]
```

Compares the hand-implemented solver (`nn_2opt` for `fleet_size == 1`,
`clarke_wright_2opt` for `fleet_size > 1`) against the OR-Tools benchmark
oracle: solution quality (`gap_pct`, positive means the hand-implemented
solver costs more) and runtime, per instance. Default `--out
docs/report/benchmark_results.csv`.

## Everything together: `make reproduce`

```bash
make reproduce
```

Runs `network build` → `batch` → `figures` → `sensitivity` → `benchmark`
in sequence, from a cold cache if necessary, regenerating every number
and figure the written-up report depends on. See
`docs/stages/stage-09-hardening.md` for measured timing.
