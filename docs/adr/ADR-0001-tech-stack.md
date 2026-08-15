# ADR-0001 — Fixed technical stack

## Status

Accepted (decided by the authors before Stage 0; recorded here rather than
re-litigated).

## Context

The project needs a stack that is reproducible on a fresh machine, coherent
across ten stages of incremental work, and defensible in a viva — every
choice should be one the authors can explain and justify, not a default
picked by a tool.

## Decision

| Concern | Decision |
|---|---|
| Language | Python 3.11+ |
| Graph / map data | OSMnx + OpenStreetMap, NetworkX for path finding |
| Map / UI | Streamlit + Folium/Leaflet (`streamlit-folium`, `folium.plugins.Draw`), built last (Stage 10) |
| Solver, v1 | Single-vehicle TSP: hand-implemented Nearest Neighbour + 2-opt |
| Solver, later | Multi-vehicle / capacity / time windows, OR-Tools as a benchmark oracle (Stage 8) |
| Live traffic APIs | Not used — disruptions are simulated on the graph, deliberately, for reproducibility |
| Plotting | Matplotlib for report figures, Folium for interactive maps |
| Testing | pytest |
| Packaging | `pyproject.toml` + `uv`/`pip` with a pinned `requirements.txt` lockfile |

## Consequences

- **Reproducibility over live realism.** No live traffic API means results
  are deterministic and re-runnable years later, at the cost of not
  reflecting real-time conditions. This is the correct trade-off for a
  piece of coursework whose evidence must be regenerable exactly.
- **Hand-implemented solver first.** NN + 2-opt is simple enough to defend
  verbally and debug by hand on a tiny fixture, at the cost of solution
  quality versus a mature solver. OR-Tools is added later specifically to
  quantify that gap, not to replace the hand-implemented solver.
- **UI last.** Because Streamlit/Folium are added only in Stage 10, every
  capability must be provable from the CLI first — this is enforced
  structurally (§2.1 of the project brief), not just by convention.
- **OSMnx pin.** OSMnx 2.x (the version resolved at Stage 0 setup time) has
  API differences from 1.x in places (e.g. graph download call signatures).
  This is absorbed in Stage 1, which is where those calls are first written
  against a concrete installed version, pinned in `requirements.txt`.

## Alternatives considered

- **A commercial routing/traffic API** (e.g. Google/TomTom) instead of
  simulated disruptions — rejected: costs money, requires network access
  and API keys for every reproduction of the report, and defeats the
  purpose of a controlled, seeded experiment.
- **A mature open-source VRP solver as the primary method** (e.g. OR-Tools
  or VROOM from the start) — rejected for v1: the brief requires a solver
  the authors can defend line-by-line in a viva; a hand-implemented
  heuristic serves that better than a black-box solver, which is instead
  used later as a benchmark.
- **Dash instead of Streamlit** — rejected: Streamlit's script-rerun model
  and `st.cache_resource`/`st.cache_data` map more directly onto "thin
  client calling a CLI-equivalent pipeline" with less boilerplate.
