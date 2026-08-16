# ADR-0002 — Fetch OSM data via `curl` subprocess, not OSMnx's built-in HTTP client

## Status

Accepted (Stage 1).

## Context

`dlm.network.loader.build_graph` needs to download the Dublin road network
from the public Overpass API. OSMnx provides `graph_from_bbox`, which
handles this end-to-end using its own `requests`-based HTTP client.

During Stage 1 development, `graph_from_bbox` (and direct `requests` calls
to the same endpoints) were found to be unreliable in this project's
sandboxed development/CI environment: requests to `overpass-api.de` would
either have their connection reset partway through, or — more seriously —
hang indefinitely without ever raising an exception or honouring an
explicit `requests` timeout. A hang that never raises cannot be retried,
which made the problem unrecoverable at the application level.

Diagnosis (via `faulthandler` stack dumps on a stuck process) traced part
of the cause to OSMnx's `_config_dns` behaviour: before querying, it
monkeypatches `socket.getaddrinfo` to pin the Overpass hostname to one
resolved IP address (via `socket.gethostbyname`), so that its own
slot-management pause logic and the actual query always hit the same
backend server despite Overpass's round-robin DNS. In this environment,
outbound HTTPS is routed through an egress proxy that resolves hostnames
itself; pre-resolving and pinning a raw IP client-side interacts badly with
that setup.

By contrast, `curl` invoked directly against the exact same URL was
empirically far more reliable across repeated tests: it either succeeded
within a few seconds, or failed within its own `--max-time` bound with a
clear error — never hung indefinitely. This made a straightforward
retry-with-backoff loop possible, which is not possible around a call that
never returns.

## Decision

`dlm.network.loader` builds the Overpass QL query itself and fetches it
with `curl` via `subprocess.run`, then hands the resulting raw OSM XML file
to OSMnx's own public `ox.graph_from_xml(...)` to build the graph. This
keeps every part of the pipeline that is not the network fetch itself
(query filter construction, XML parsing, simplification, largest strongly
connected component extraction, travel-time imputation) as unmodified
OSMnx/NetworkX behaviour:

- The Overpass "drive" filter string is obtained from OSMnx's own
  `osmnx._overpass._get_network_filter("drive")` — a pure string builder
  with no I/O — so it is guaranteed identical to what `graph_from_bbox`
  would use, rather than a hand-copied string that could silently drift
  from it on an OSMnx upgrade.
- `_fetch_overpass_xml` retries up to 8 times with backoff on any curl
  failure (non-200 HTTP status, or curl's own error).
- The fetched XML is written to a temp file, parsed via
  `ox.graph_from_xml(bidirectional=False, simplify=...)`, and the temp file
  is deleted afterward — it is not itself cached, since the final
  `.graphml` cache (keyed by bbox/network_type/simplify/OSMnx version)
  already makes repeat builds fast.

## Consequences

- **Requires `curl` on `PATH`.** A reasonable assumption for this
  project's dev/CI/report-generation environment (curl is close to
  universal on Linux/macOS CI runners), but worth stating as a portability
  constraint rather than leaving it implicit.
- **One more moving part to explain in a viva**, but a simpler one than
  the alternative considered (monkeypatching OSMnx's internal `requests`
  call): "we build the query, fetch it with curl, and hand the XML to
  OSMnx's own public loader" is a complete, self-contained explanation that
  doesn't require describing OSMnx's private HTTP internals at all.
- **Portable if the environment changes.** If this project is later run
  somewhere OSMnx's own transport is reliable, `graph_from_bbox` could be
  swapped back in directly — the curl-based path is not slower and
  produces an identical graph (same filter, same XML→graph conversion), so
  there's no urgency to do so, but it isn't a one-way door either.

## Alternatives considered

- **Monkeypatching `osmnx._http._config_dns` to a no-op, keeping OSMnx's
  `requests`-based transport otherwise** — reduced but did not eliminate
  the hangs (a plain data-endpoint request could still hang indefinitely
  even with DNS pinning disabled); rejected once curl proved reliably
  fast-failing where `requests` did not.
- **Increasing `requests`/urllib3 timeouts, or setting `socket.setdefaulttimeout`** —
  tested; neither reliably bounded the hang in this environment.
- **A different public Overpass mirror** (`overpass.kumi.systems`,
  `lz4.overpass-api.de`, `overpass.osm.ch`) — tested; each had its own
  problem (aggressive rate limiting, empty/regional-only data, or the same
  hang), and none were more reliable than curl-against-the-main-instance.
