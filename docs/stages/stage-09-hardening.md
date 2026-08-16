# Stage 09 — Hardening

## Goal

Close the loop the previous eight stages left open: make the whole
pipeline runnable end to end with one command from a cold cache
(`make reproduce`), consolidate every stage's honestly-reported
limitations into one document, document the full CLI surface, and verify
— not just assert — that the project installs and passes its test suite
on a genuinely independent environment.

## Scope

**In scope:**
- `make reproduce`: chains `dlm network build` -> `dlm batch` ->
  `dlm figures` -> `dlm sensitivity` -> `dlm benchmark`, regenerating
  every number and figure the report depends on from a cold cache.
- `make network`: fixed a stale stub left over from before Stage 1 (see
  Results — it still printed "lands in Stage 1" and exited 1, even
  though `dlm network build` has existed since Stage 1's `cli.py`).
- `docs/limitations.md`: consolidated from the "Known limitations"
  section of all nine `docs/stages/stage-0N-*.md` files, organised by
  theme instead of by discovery order.
- `docs/cli.md`: a full reference for every `dlm` subcommand.
- `docs/architecture.md`: rewritten from "first draft... planned
  pipeline" to describe the real, now-complete data flow, and to record
  what each stage actually added to it.
- A hardening/QA pass: fresh-venv install + full test suite on an
  independent Python environment (closing Stage 0's own flagged
  limitation), and a determinism check on `make reproduce`'s outputs.

**Explicitly out of scope** (land in Stage 10):
- The Streamlit UI (`app/`) and `make app`.
- `tests/test_cli_ui_parity.py` — it asserts CLI/UI parity, which has no
  meaning until the UI exists.

## Design

**`make reproduce` is a thin sequential wrapper, not a new code path.**
Every one of its five steps is an existing, already-tested `dlm`
subcommand; Stage 9 added no new solving, disruption, or metric logic —
consistent with the project's own architectural law that `dlm.cli` is
the only door into the pipeline (`docs/architecture.md`). Each step
builds/caches whatever graph or matrix it individually needs
(`build_graph()` is called fresh in `batch`, `sensitivity`, and
`benchmark`, each hitting the same cache key), so the target is safe to
run from a completely cold `data/cache/` as well as a warm one.

**Fixing `make network` is a real, if small, bug fix.** The Makefile's
`network` target was written in Stage 0 as a stub that printed "lands in
Stage 1" and exited 1. Stage 1 built `dlm network build` and wired it
into the CLI, but nobody updated the Makefile target that was supposed
to call it — it silently kept failing for eight stages without being
noticed, because nothing in the test suite or any stage's acceptance
checklist exercises `make network` itself (only `dlm network build`
directly). Found by grep during this stage's investigation, fixed by
pointing it at `dlm network build`, same pattern as `experiment`/
`figures`.

**`docs/limitations.md` is compiled, not re-derived.** Every bullet in it
already existed, verbatim or near-verbatim, in a stage doc; Stage 9's job
was to read all nine, group by theme (network/instances/matrix/solver/
disruption/information-model/batch/fleet, plus a project-wide
cross-cutting section for things that don't belong to one stage — single
day horizon, static demand, no live traffic feed, single bounding box),
and add nothing invented. Re-deriving limitations from scratch at this
stage — rather than compiling what was already found and reported live —
would risk missing something a stage doc already knew, or worse,
contradicting it.

**The fresh-venv check is a real second environment, not the same one
re-run.** `make setup` had been run once, in Stage 0, in this project's
one working `.venv/`; every stage since has reused it. That never
actually tested Stage 0's own flagged concern ("`requirements.txt`...
has not yet been verified installable on a second, independent
machine"). This stage creates a brand-new `venv` in an unrelated
directory, installs `.[dev,ui,fleet]` into it with no access to the
working `.venv`'s installed packages or pip cache reuse beyond whatever
pip's own HTTP cache already had, and runs the full test suite against
it — the closest approximation of "a second machine" available inside
this environment.

## Interfaces

No new Python modules or CLI commands. `docs/cli.md` documents the
existing surface; `Makefile`'s `network` and `reproduce` targets are the
only functional changes.

## How to run

```bash
make setup        # if not already done
make reproduce    # ~10 minutes from a warm graph/matrix cache
```

Or any individual stage of it:

```bash
make network                                    # dlm network build
dlm batch --instances small,medium,large         # writes docs/report/batch_results.csv
dlm figures                                      # writes docs/report/figures/*.{png,svg}
dlm sensitivity                                  # writes docs/report/sensitivity_results.csv
dlm benchmark --instances small,medium,large,fleet  # writes docs/report/benchmark_results.csv
```

## Acceptance criteria and evidence

**`make network` no longer stubs out.**

Before this stage:
```
$ make network
dlm network build: lands in Stage 1 (docs/stages/stage-01-network.md)
make: *** [network] Error 1
```

After:
```
$ make network
cache:            data/cache/dublin_drive_664cee449591eb29.pkl (hit)
build time:       0.86s
nodes:            13333 (dropped 412 outside largest strongly connected component)
edges:            33832 (dropped 1074)
maxspeed real:    24741/33832 (73.1%)
maxspeed imputed: 9091/33832
```

**`make reproduce` runs end to end from this project's real cache and
regenerates every committed report artefact.**

```
$ time make reproduce
...
instances:         small, medium, large
scenarios:         4 curated + 10 random
runs:              42
T2(reactive) feasible: 34/42
T3 feasible:       34/42
mean Saving %:     0.0%
written to:        results/batch-20260816T161519Z
also written to:   docs/report/batch_results.csv
wrote t1_t2_t3_comparison.png / t1_t2_t3_comparison.svg
wrote feasibility_breakdown.png / feasibility_breakdown.svg
wrote saving_distribution.png / saving_distribution.svg
written to:        docs/report/figures
small (drive time fixed at 1820.0s): ...
medium (drive time fixed at 6652.2s): ...
large (drive time fixed at 11353.2s): ...
written to:        docs/report/sensitivity_results.csv
small: hand=1820.0s (0.7ms)  or-tools=1793.4s (10.07s)  gap=+1.5%
medium: hand=6652.2s (18.6ms)  or-tools=6624.9s (10.00s)  gap=+0.4%
large: hand=11353.2s (87.6ms)  or-tools=9803.0s (10.00s)  gap=+15.8%
fleet: hand=8856.2s (0.9ms)  or-tools=8695.9s (10.00s)  gap=+1.8%
written to:        docs/report/benchmark_results.csv

real    9m58.0s
```

**`make reproduce`'s outputs are deterministic.** `dlm batch` and
`dlm sensitivity` were each run twice back to back (same seeds, same
graph); the resulting `batch_results.csv` and `sensitivity_results.csv`
were byte-identical both times (`diff` reported no differences). This is
exactly what Stage 0's determinism policy (§ "Determinism and caching",
`docs/architecture.md`) promises and Stage 9 is the first stage to
actually check it end to end rather than per-module.

**Fresh, independent environment: install + full test suite.** A second
`venv`, unrelated to this project's working `.venv/`, was created and
`pip install -e ".[dev,ui,fleet]"` run against it with no errors:

```
$ python3 -m venv /tmp/.../fresh-venv-test
$ /tmp/.../fresh-venv-test/bin/pip install -e ".[dev,ui,fleet]" -q
(no output — clean install)
$ /tmp/.../fresh-venv-test/bin/dlm --version
dlm 0.1.0
$ /tmp/.../fresh-venv-test/bin/pytest -q
........................................................................ [ 49%]
........................................................................ [ 98%]
..                                                                       [100%]
146 passed in 148.57s (0:02:28)
```

All 146 tests accumulated across Stages 0-8 pass unmodified on this
independent install — closing the concern Stage 0's own "Known
limitations" flagged and never re-tested until now.

**`docs/cli.md` covers every command.** Cross-checked against
`src/dlm/cli.py`: `network build`/`stats`; `instance new`/`add`/
`random`/`remove`/`move`/`rename`/`list`/`show`/`map`/`matrix`; `plan`;
`compare`; `disrupt list`/`validate`/`preview`/`new`; `batch`;
`figures`; `sensitivity`; `benchmark` — 20 commands, all documented with
their real option names and defaults, none invented.

## Results

| Step | Wall time (warm cache) |
|---|---|
| `dlm network build` | <1s (cache hit) |
| `dlm batch` (small+medium+large, 4 curated + 10 random scenarios = 42 runs) | ~7 min |
| `dlm figures` | <1s |
| `dlm sensitivity` (3 instances x 5 service-time values) | ~4s |
| `dlm benchmark` (small+medium+large+fleet, 10s OR-Tools budget each) | ~40s |
| **`make reproduce` total** | **~10 min** |

`dlm batch` dominates the total: it recomputes a fresh disrupted graph
per (instance, scenario) pair rather than caching across scenarios (a
documented Stage 7 limitation, unchanged here — not worth revisiting at
this project's scale, see `docs/limitations.md`). `dlm benchmark`'s
runtime is dominated by its configured `--time-limit` (default 10s per
instance x 4 instances = up to 40s of pure OR-Tools search), not solution
difficulty — also already documented (Stage 8) and unchanged.

The `make network` fix and the two-run determinism check are new
findings from this stage, not carried over from an earlier one.

## Known limitations

- **The fresh-venv install check approximates "a second machine" but
  isn't literally one** — it's a second, independent Python virtual
  environment inside the same container/OS/filesystem as the project's
  working `.venv/`, sharing the same system Python, system libraries, and
  network path. It rules out "only works because of stray state in
  `.venv/`" bugs, which is what Stage 0 actually flagged concern about,
  but it cannot catch an OS- or architecture-specific packaging problem.
- **The determinism check covers `dlm batch` and `dlm sensitivity`, not
  `dlm benchmark`.** OR-Tools' search is itself not required to be
  bit-for-bit deterministic run to run within a fixed time budget (it is
  a real-time metaheuristic budgeted in wall-clock seconds, not
  iterations) — re-running `dlm benchmark` twice was not expected to, and
  was not checked to, produce identical `ortools_total_time_s` values.
  The hand-implemented solver side of that same CSV *is* deterministic
  (it inherits `dlm batch`/`sensitivity`'s guarantee via the same
  `TwoOptSolver`/`ClarkeWrightSolver` code path), only the OR-Tools
  column is exempted here.
- **`make reproduce`'s ~10-minute runtime is dominated by `dlm batch`'s
  lack of cross-scenario graph caching** (Stage 7's documented
  limitation) — not revisited in this stage, since it doesn't block
  reproducibility, only its speed, and the project's own scale doesn't
  need it faster.

## Next

Stage 10 depends on:
- Every capability it will expose already existing as a tested `dlm`
  command (`docs/cli.md`) — the architectural law
  (`docs/architecture.md`) that Stage 9 is the last checkpoint for.
- `docs/limitations.md` as the source `app/`'s own UI copy (if any
  limitations need surfacing to a user) should quote from, not
  re-derive.

Stage 10 will build the Streamlit UI (`app/main.py`, `app/state.py`) as a
thin client calling the same `dlm.*` functions `dlm.cli` calls, and
`tests/test_cli_ui_parity.py` will assert identical `T1`/`T2`/`T3`
between the CLI path and the UI path for the same inputs.
