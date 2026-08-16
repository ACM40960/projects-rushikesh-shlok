# Glossary

Terms are added as the stage that introduces them lands.

- **Depot** — the vehicle's start/end point. Chosen the same way as any
  stop (address, lat/lon, preset, random). See `docs/stages/stage-02-instances.md`.
- **Stop** — a delivery location the vehicle must visit. Has a label,
  coordinates, a snapped graph node, and a `source` recording how it was
  chosen. See `docs/stages/stage-02-instances.md`.
- **Instance** — a depot + a set of `N` stops (+ fleet size `K`), the unit
  the solver operates on. `N` is always a property derived from the stop
  list, never a stored constant. See `docs/stages/stage-02-instances.md`.
- **Snapping** — mapping a free-form lat/lon (from a click, a geocoded
  address, or a raw coordinate) to the nearest routable graph node, with a
  maximum-distance guard. See `docs/stages/stage-01-network.md`.
- **Travel-time matrix** — the all-pairs shortest-time (and shortest-path)
  lookup between every pair of instance points, cached and incrementally
  updatable. See `docs/stages/stage-03-matrix.md`.
- **Solution** — an ordered visit sequence plus its expanded route legs,
  total time, and total distance, returned by a solver. See
  `docs/stages/stage-04-baseline.md`.
- **Disruption / Scenario** — a named, YAML-defined change to the graph
  (edge/node/corridor/polygon closure, or a slow zone), with optional time
  bounds and severity. A `Scenario` is a list of `Disruption`s. See
  `docs/stages/stage-05-disruptions.md`.
- **Information model** — `dlm.simulation.execution.InformationModel`
  (`omniscient` / `reactive`): what the driver of the *original* planned
  route knows about a disruption and when, which is what makes `T2`
  well-defined. `infeasible` (anticipated here as a third enum value
  before Stage 6's implementation) turned out to read more naturally as a
  separate `feasible: bool` outcome flag on `T2Result`/`T3Result` — a
  route can be infeasible under *either* information model, so it isn't a
  third kind of driver knowledge, just a possible outcome of the other
  two. See `docs/modelling.md` and `docs/stages/stage-06-experiment.md`.
- **T1 / T2 / T3 / T3_oracle / Saving %** — the core evaluation metrics; see
  the root `README.md` for the one-line definitions and `docs/modelling.md`
  for the precise ones.
