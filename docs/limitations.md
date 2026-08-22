# Limitations

## Network and travel time

- The graph is a static OpenStreetMap snapshot and depends on OSM coverage,
  tagging accuracy and the selected bounding box.
- The bounding box covers Greater Dublin but not the full commuter region.
- There is no live traffic, incident feed, signal-delay, queueing or weather model.
- Missing speed tags use static free-flow assumptions rather than observed speeds.
- The committed pickle is trusted only because its SHA-256 digest is checked;
  arbitrary pickle uploads are not supported.

## Routing models

- Nearest-neighbour, 2-opt and Clarke–Wright are heuristics and have no global
  optimality guarantee.
- OR-Tools is run with a finite time limit and is a benchmark solver, not proof
  of an optimum.
- The hand-written solvers do not enforce time windows; time-window support is
  demonstrated only in OR-Tools.
- There is no lateness penalty.
- Fleet routing is supported for T1 baselines, but T2/T3 disruption simulation
  is single-vehicle only.
- Clarke–Wright can leave stops unassigned even where another capacity packing
  might serve them.

## Disruptions and replanning

- Scenarios are simulated rather than live forecasts. Library geometry is
  illustrative unless a scenario explicitly includes a verifiable source.
- `severity` is informational and has no routing effect.
- Scenario time windows affect routing only when callers pass `at_time`; the CLI
  and app now expose it, while code that explicitly passes `None` applies all
  disruptions regardless of their windows.
- Slow zones change edge costs but do not trigger T3 stop-order reoptimisation.
  T3 is triggered only when reactive execution encounters a removed edge.
- T3 keeps the already served prefix fixed and optimises only the remaining path.
- No interactive disruption-geometry editor is provided; users select saved
  scenarios or adjust the speed factor of an existing slow zone.
- Uniform network-wide disruptions rarely intersect a small delivery route.
  Targeted stress tests demonstrate behaviour conditional on intersection and
  must not be presented as typical Dublin outcomes.

## Metrics

- A constant 180-second default service time is assumed when a stop has no
  explicit value; it is not estimated from observations.
- Fuel use and emissions are not modelled or optimised.
- Distance, time and saving values depend on the pinned graph, modelled speeds,
  service-time assumption and heuristic route found.
- The internal field `T3_oracle` is retained for compatibility but denotes a
  full-knowledge heuristic, not a mathematical oracle or guaranteed bound.

## Application scope

- Streamlit is single-user coursework software, not a production dispatcher.
- There is no authentication, concurrent editing, persistence service,
  operational monitoring, driver app or deployment hardening.
- User-created instances are local JSON files; simultaneous writers are not supported.
