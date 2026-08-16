# ADR-0003 — Expand the default download area to Greater Dublin

## Status

Accepted (Stage 2; supersedes the smaller bbox Stage 1 shipped with).

## Context

Stage 1 shipped with `DEFAULT_BBOX` covering Dublin city centre, UCD
Belfield, and Dublin Port — deliberately smaller than the M50 catchment,
for fast/reliable first downloads. That stage's own documentation raised
the city-centre-vs-M50 trade-off as an open question, to be revisited
"after Stage 3, once matrix build times are known."

Stage 2 surfaced a more immediate, concrete reason to revisit it sooner:
curating ~30 "recognisable Dublin locations" (brief's own phrasing, for
the preset list) naturally includes things like Dublin Airport, and outer
suburbs like Tallaght, Blanchardstown, Swords, and Dun Laoghaire — all
standard answers to "name a well-known Dublin place." Geocoding these
confirmed several fall outside the original bbox entirely, meaning those
presets would fail to snap to any node at all (not a close-call — genuinely
no graph data existed there).

## Decision

`DEFAULT_BBOX` (`dlm.network.loader`) is expanded to
`(-6.4200, 53.2800, -6.1000, 53.4700)` — Greater Dublin: city centre, UCD,
Dublin Port, Dublin Airport, and the Tallaght / Blanchardstown / Dun
Laoghaire / Swords suburbs. Still short of the full M50 catchment.

This was tested before committing to it: a full download+build of this
larger area completed reliably in the same session (curl fetch ~14s for a
~29MB response, OSMnx graph build ~42s), using the same ADR-0002 curl-based
fetch path — the bbox expansion did not reintroduce the original Overpass
reliability problem.

## Consequences

- **Graph is ~2.6x larger** (28,112 nodes / 62,068 edges post-SCC, up from
  10,899 / 24,837). Download+build time increased proportionally
  (roughly 30s → roughly 65-80s, observed with retries).
- **Forced a second change**: the larger graph took **10.42s** to load
  from the `.graphml` cache — past Stage 1's own <5s acceptance bar. Cache
  format was changed to pickle (0.44-1.3s load) as a direct consequence.
  See `docs/stages/stage-02-instances.md`'s "Amendments to Stage 1" and
  the docstring at the top of `dlm.network.loader` for the reasoning.
- **All 30 curated presets now snap successfully** within the default
  150m guard (verified directly, not assumed, during preset curation).
- Real Dublin OSM `maxspeed` coverage in the larger graph is 89.8% (down
  slightly from the smaller area's 98.2% — the outer suburbs and airport
  access roads are less densely tagged than the city centre, which is
  expected and unsurprising, not a data-quality concern).

## Alternatives considered

- **Keep the smaller bbox; drop the out-of-area presets** — rejected: a
  curated "recognisable Dublin locations" list without the airport or
  Tallaght is a materially worse list for the project's own stated
  purpose (demos fast, report instances legible), and the fix (a bigger
  bbox) turned out to be cheap.
- **Defer to Stage 3 as originally planned** — rejected once it became
  clear the preset list (a Stage 2 deliverable, not a Stage 3 one)
  concretely needed the larger area now; waiting would have meant either
  a materially worse preset list for this stage or redoing preset
  curation work in Stage 3.
