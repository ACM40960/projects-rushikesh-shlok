# Modelling choices

## Network and route cost

The road network is a directed `networkx.MultiDiGraph`. Each edge has length in
metres and estimated travel time in seconds. The travel-time matrix contains
directed shortest-path costs, paths and along-path distances for the depot and
selected delivery stops.

For a served stop order \(R\), total time is

\[
T(R)=\sum_{(i,j)\in R} t_{ij}+\sum_{i\in S_R}s_i.
\]

The solver optimises driving time. Service time is added by the metrics layer.
A default service time of 180 seconds is used as an explicit modelling
assumption. Sensitivity analysis shows that service time contributes
approximately 35–44% of T1 at this default and therefore materially affects
the reported total-time values. The value is not estimated from observed
delivery data.

Service time is constant with respect to ordering only when the same set of
stops is served. After an infeasible T2 execution, only completed deliveries
are counted as served and charged service time; later legs are not evaluated.
There is no lateness-penalty term.

## Baseline heuristics

The main single-vehicle solver constructs a nearest-neighbour order and applies
first-improvement 2-opt. Because Dublin's network is directed, every candidate
reversal is evaluated using the full directed route cost; the symmetric-TSP
constant-time delta formula is not valid.

The fleet baseline uses parallel Clarke–Wright savings, capacity-feasible
merges and per-route 2-opt. This is a heuristic and may leave stops unassigned
when a different packing exists. Fractional demands are represented in
OR-Tools using one shared decimal scale, avoiding the previous independent
rounding that could turn a positive demand into zero.

## Time windows

The hand-written nearest-neighbour, 2-opt and Clarke–Wright solvers do not
enforce time windows. OR-Tools demonstrates VRPTW support and includes service
time in its schedule-transit callback. It may drop an impossible stop using a
large disjunction penalty. No result should imply that lateness penalties are
included: they are not.

## Disruptions

A `Scenario` contains one or more edge, node, corridor or polygon disruptions.
A closure removes resolved directed edges. A slow zone multiplies travel time
by `1 / speed_factor`; for example, `0.5` means half the original speed and
twice the edge travel time. Optional time windows are evaluated using the
`at_time` passed through the engine, CLI and Streamlit app.

`severity` is descriptive metadata only. It does not change edge cost,
probability or route choice. Slowdown intensity is controlled only by
`speed_factor`.

## T1, T2 and T3

- **T1** is the baseline route under the normal graph.
- **T2 omniscient** keeps the stop order but recomputes every leg using the
  disrupted graph before departure.
- **T2 reactive** follows each original path until it reaches a removed edge,
  then attempts a shortest-path detour from that node. If a required leg is
  impossible, execution stops; later legs are not evaluated.
- **T3** starts from the first reactive blockage, keeps the already served
  prefix and reorders the remaining stops using a path-specific directed
  2-opt objective: `blockage -> remaining stops -> real depot`.

T3 is blockage-triggered. A slow zone changes T2 cost but, because its edges
remain traversable, does not trigger stop-order reoptimisation.

The internal compatibility key `T3_oracle` denotes a from-scratch heuristic
solution with full disruption knowledge. It is not a proof of optimality or a
mathematical oracle. Because both solutions are heuristic, it is not guaranteed
to be a strict bound on T3.

## Feasibility and saving

A comparison is infeasible when a required route segment has no directed path.
Infeasible `drive_time_s`, `distance_m` and `total_time_s` values are `None`,
not zero. The failed leg is retained for diagnosis, and no subsequent leg is
simulated.

For feasible reactive T2 and T3 values,

\[
\operatorname{Saving}(\%)=\frac{T_2-T_3}{T_2}\times100.
\]

Saving is undefined when either side is infeasible.

## Fuel and emissions

The optimisation objective is total route time. Fuel consumption and emissions
are not directly modelled. Distance is reported, but it is not converted to a
fuel or carbon claim because vehicle efficiency, load, idling and speed effects
are outside the model.
