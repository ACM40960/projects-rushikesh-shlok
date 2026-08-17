# Disruption-Aware Last-Mile Routing on Dublin's Road Network

UCD ACM40960 — Projects in Mathematical Modelling.
Authors: Shlok Shetty, Rushikesh Mane.

## What this project does, in plain terms

Imagine a delivery van leaving a depot in Dublin with a list of stops to
make. This project plans that route on Dublin's real streets (not a made-up
grid), then asks a simple question: **what happens if a street on that route
gets closed after the van has already left?**

The user picks the stops (by address, map coordinates, a preset location,
or a random sample), and can then simulate a disruption — a closed road, a
parade, roadworks, a protest. The system then compares three numbers:

| Symbol | In plain terms |
|---|---|
| `T1` | How long the original plan takes, if nothing goes wrong |
| `T2` | How long that *same, unchanged* plan takes once the road is closed (the driver has to detour) |
| `T3` | How long a *freshly re-planned* route takes instead, starting from wherever the driver got blocked |

```
Saving(%) = (T2 - T3) / T2 × 100
```

In other words: **does it actually pay off to re-plan on the fly, or is
sticking with the original route just as good?** That is the question this
project answers with real, measured numbers — not a guess.

This is software that produces evidence, not a paper making a claim. For
the exact definitions behind `T1`/`T2`/`T3`, see `docs/modelling.md`. For
how the pieces of code fit together, see `docs/architecture.md`.

## Tools

- **Python 3.11+**
- **OSMnx + NetworkX** — download Dublin's real street map from
  OpenStreetMap and find shortest paths on it
- **A hand-written route solver** (Nearest-Neighbour + 2-opt — two classic,
  simple route-planning techniques) as the main method, with **OR-Tools**
  (a professional-grade solver library) used only to double-check how close
  the hand-written one gets to a much stronger benchmark
- **Streamlit + Folium** for the web app (a thin visual layer built last,
  on top of a pipeline that already worked from the command line)

## Try it yourself

```bash
git clone <repo-url>
cd Maths-Modelling
make setup   # creates a virtual environment, installs everything needed
make test    # runs the automated test suite
```

Once set up:

```bash
make reproduce   # regenerates every number and chart used in the report (~10 min)
make app         # opens the interactive web app in your browser
```

## How the code is organised

```
src/dlm/          the actual pipeline: map, delivery instances, solver,
                   disruptions, simulation, charts, command-line tool
app/              the web app (Stage 10) — just a visual front end,
                   no logic of its own
scenarios/        saved disruption examples (as readable YAML files)
data/             cached maps/matrices (not stored in git) + example
                   delivery instances that are stored in git
results/          output from each run (not stored in git)
tests/            automated tests, including some with hand-checked
                   "known correct" answers
docs/             write-ups: how it's built, the maths behind it, where
                   the map data comes from, one document per project stage
```

## Project stages — all complete

This project was built in eleven stages, each one working end-to-end
before the next began:

| # | Stage | What it added |
|---|---|---|
| 0 | Foundations | Project setup, tooling, tests |
| 1 | Dublin road network | Downloads and prepares the real street map |
| 2 | Dynamic instances | Lets a user choose delivery stops |
| 3 | Travel-time matrix | Pre-computes travel time between every pair of stops |
| 4 | Baseline solver | Plans a route (the "before disruption" plan) |
| 5 | Disruption engine | Lets a user simulate a closed road, parade, etc. |
| 6 | Experiment core | Computes `T1` / `T2` / `T3` and the saving |
| 7 | Results harness | Runs many scenarios and produces charts |
| 8 | Fleet & benchmark | Supports multiple vehicles; checks against OR-Tools |
| 9 | Hardening | Tidies up, documents known limitations, checks reproducibility |
| 10 | UI | The Streamlit web app |

Each stage has its own write-up in `docs/stages/` — what it set out to do,
how it was built, how it was checked, and what its known limitations are.
