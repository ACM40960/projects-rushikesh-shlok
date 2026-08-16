# ADR-0004 — Default per-stop service time

## Status

**Proposed** — pending author confirmation. Unlike ADR-0002/0003, this is
not a technical/environment decision the engineer can resolve unilaterally;
it's a modelling assumption the brief (§9) explicitly asks the authors to
set, since it directly shapes every `T1`/`T2`/`T3` number in the report.

## Context

`T1` (`dlm.simulation.metrics.compute_t1`) is driving time plus service
time. `Stop.service_time_s` has existed in the schema since Stage 2 but
has never been set to anything but its default, `0.0` — no input mode
(address, preset, random, map-click) collects it. Left at `0.0`, `T1`
would just be driving time relabelled, making the "service time" line in
every report table trivially zero and the distinction meaningless.

## Decision (proposed)

`dlm.config.settings.default_service_time_s = 180.0` (3 minutes) is
substituted whenever a stop's own `service_time_s` is `0.0` (i.e.
whenever it hasn't been explicitly set — see the convention note below).
180s is a common assumption for last-mile parcel drop-off in the
literature and industry rules of thumb (a few minutes to park, locate the
address, and hand off or leave a parcel) but is **not derived from any
Dublin-specific data** — it's a placeholder chosen so the pipeline
produces a non-degenerate `T1` while this stage was built, not a
researched figure.

**Convention**: `0.0` is treated as "unset, use the default" rather than
"explicitly zero service time." This is workable today because nothing
sets a non-zero, genuinely-intentional `0.0` — but it becomes ambiguous
the moment a real use case wants to say "this stop truly has no service
time" (e.g. a drive-by inspection stop). If that need arises, the schema
should grow an explicit `service_time_s: float | None` (`None` = unset,
`0.0` = intentional zero) rather than continue overloading `0.0`.

## Consequences

- Every `T1`/`T2`/`T3` figure in the report is sensitive to this number.
  Changing it later means every result must be regenerated
  (`make reproduce`, Stage 9) — cheap computationally, but worth knowing
  before the report's numbers are treated as final.
- Because it's a single global default rather than per-stop or
  per-category (hospital vs. residential drop-off, say), it doesn't
  capture real variation in delivery complexity. Acceptable for this
  project's scope; flagged in `docs/limitations.md` (Stage 9).

## Open question for the authors

1. Keep 180s, or pick a different fixed value?
2. Fixed for every stop, or size/category-dependent (per §9's own
   phrasing — e.g. hospitals or large retail might realistically take
   longer than a residential drop-off)? Category-dependent would use the
   `category` field already present on presets (`dlm.instance.presets`)
   as a natural signal, but stops added by address/lat-lon/random have no
   category today — would need one added, or a fallback rule.

Recommendation: keep the fixed 180s default for now (simplicity,
consistent with "prefer a clear implementation over a clever one") and
revisit only if the report's findings turn out to be sensitive to this
choice — worth a quick sensitivity check in Stage 7's batch experiments
rather than guessing now.
