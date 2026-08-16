# Disruption-Aware Last-Mile Routing on Dublin's Road Network

UCD ACM40960 — Projects in Mathematical Modelling.
Authors: Shlok Shetty, Rushikesh Mane.

## What this is

A fleet of `K` vehicles leaves a depot in Dublin, visits `N` delivery stops
**chosen by the user at runtime** (address, lat/lon, curated preset, or a
seeded random sample), and returns to the depot. A route is planned on the
real Dublin street network (OpenStreetMap via OSMnx). The user can then
simulate a disruption — a closed street, a parade, a protest, roadworks —
and the system computes three numbers:

| Symbol | Meaning |
|---|---|
| `T1` | Cost of the planned route under normal conditions |
| `T2` | Cost of *the same planned route* after the disruption |
| `T3` | Cost of the *re-optimised* route after the disruption |

```
Saving(%) = (T2 - T3) / T2 × 100
```

The project is not a research paper — it is the software that produces the
evidence for one. See `docs/modelling.md` (from Stage 4 onward) for the
precise definitions, and `docs/architecture.md` for how the pieces fit
together.

## Stack

Python 3.11+, OSMnx + NetworkX for the routable graph and path finding,
hand-implemented Nearest-Neighbour + 2-opt as the primary solver (OR-Tools
as a benchmark oracle from Stage 8), Streamlit + Folium for the UI (built
last, Stage 10, as a thin client over an already-correct CLI pipeline).

## Quickstart

```bash
git clone <repo-url>
cd Maths-Modelling
make setup   # creates .venv, installs the package + dev/ui/fleet extras, installs pre-commit
make test    # runs the test suite
```

Once later stages land, the full pipeline will run end-to-end via:

```bash
make reproduce   # Stage 9: regenerates every number and figure in the report
make app         # Stage 10: launches the Streamlit UI
```

## Repository layout

```
src/dlm/          the pipeline: network, instance, solver, disruption, simulation, viz, cli
app/              Streamlit UI (Stage 10) — thin client only, no domain logic
scenarios/        version-controlled disruption YAML files
data/             cached graphs/matrices (gitignored) + committed presets and instances
results/          per-run outputs (gitignored except this directory's README)
tests/            pytest suite, including fixtures with known-optimal answers
docs/             architecture, modelling, data provenance, ADRs, one doc per stage
```

## Stage status

| # | Stage | Status |
|---|---|---|
| 0 | Foundations | ✅ |
| 1 | Dublin road network | ✅ |
| 2 | Dynamic instances | ✅ |
| 3 | Travel-time matrix | ⬜ |
| 4 | Baseline solver | ⬜ |
| 5 | Disruption engine | ⬜ |
| 6 | Experiment core (T1/T2/T3) | ⬜ |
| 7 | Results harness | ⬜ |
| 8 | Fleet & benchmark | ⬜ |
| 9 | Hardening | ⬜ |
| 10 | UI | ⬜ |

See `docs/stages/` for one write-up per completed stage (goal, design,
interfaces, acceptance evidence, limitations).

## Documentation map

Start at `docs/index.md`. Key documents: `docs/architecture.md` (data flow),
`docs/modelling.md` (the maths), `docs/data.md` (OSM provenance and
licensing), `docs/limitations.md`, `docs/glossary.md`, `docs/adr/` (decision
records).
