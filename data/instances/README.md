# Committed instances

Canonical instances used throughout the report, built from named Dublin
locations (not random points) so they are legible in the write-up:
`small` (N=8), `medium` (N=20), `large` (N=40) — single-vehicle, added in
Stage 2. `fleet` (N=15, K=3 vehicles, vehicle_capacity=10, demand cycling
1/2/3 — total demand exactly matches total capacity) — multi-vehicle
CVRP, added in Stage 8.

`demo_saving` (N=6 seeded-random stops, `seed=37`) is not part of the
canonical report set — a deliberately found demo instance (paired with
`scenarios/demo_saving_showcase.yaml`) that produces a genuine, positive
`Saving %` (`dlm compare --instance demo_saving --scenario
demo_saving_showcase`), unlike every canonical instance's curated
scenarios, which measure 0% or infeasible (see `docs/limitations.md`).
Kept for demonstration purposes; not used in any report figure or table.

Its 7.4% result was found by searching many candidate instances against
one specific build of the Dublin drive network, and OSM data changes over
time — a fresh `dlm network build` on a different day can shift the
travel times just enough to erase it (the same real-world-drift issue
documented for the `fleet` instance in `docs/limitations.md`). To keep
the demo reproducible, that exact network snapshot is committed at
`data/cache/dublin_drive_664cee449591eb29.pkl` as a deliberate, narrow
exception to the usual "caches are regenerable, don't commit them" rule
(see `.gitignore`). As long as that file is present, `dlm network build`
and the UI both load it instead of re-fetching from Overpass, and
`demo_saving` reproduces exactly 7.4% every time.
