# Documentation map

This project is built stage by stage (see the root `README.md` stage status
table). Each stage's write-up lives in `docs/stages/stage-XX-<name>.md` and
follows a fixed contract: goal, scope, design, interfaces, data &
assumptions, how to run, acceptance criteria with evidence, results, known
limitations, and what the next stage depends on.

## Where to start

- **New to the project?** Read the root `README.md`, then this page, then
  `docs/architecture.md`.
- **Want the maths?** `docs/modelling.md` (objective function, T1/T2/T3
  definitions, the information-model enum). Landing from Stage 4.
- **Want to know where the data comes from?** `docs/data.md` (OSM
  provenance, licence, speed assumptions, known network limitations).
  Landing from Stage 1.
- **Wondering why a decision was made a certain way?** `docs/adr/` — one
  Architecture Decision Record per non-obvious choice.
- **Confused by a term?** `docs/glossary.md`.
- **Want the honest list of what this system does not model?**
  `docs/limitations.md`, consolidated in Stage 9 from every stage doc's
  own "Known limitations" section.
- **Want the full command reference?** `docs/cli.md`. Landing from
  Stage 9.

## Documents by stage

| Stage | Document |
|---|---|
| 0 | `docs/stages/stage-00-foundations.md` |
| 1 | `docs/stages/stage-01-network.md`, `docs/data.md` |
| 2 | `docs/stages/stage-02-instances.md` |
| 3 | `docs/stages/stage-03-matrix.md` |
| 4 | `docs/stages/stage-04-baseline.md`, `docs/modelling.md` |
| 5 | `docs/stages/stage-05-disruptions.md`, `scenarios/README.md` |
| 6 | `docs/stages/stage-06-experiment.md` |
| 7 | `docs/stages/stage-07-results.md` |
| 8 | `docs/stages/stage-08-fleet-benchmark.md` |
| 9 | `docs/stages/stage-09-hardening.md`, `docs/cli.md`, `docs/limitations.md` |
| 10 | `docs/stages/stage-10-ui.md` |
