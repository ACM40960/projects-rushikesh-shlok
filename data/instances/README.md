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
