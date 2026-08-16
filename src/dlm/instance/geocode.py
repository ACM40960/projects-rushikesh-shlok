"""Cached address geocoding against OpenStreetMap's Nominatim service.

The same address query never hits the network twice (results are cached to
disk); ambiguous queries — multiple genuinely distinct real places matching
the same text — return candidates instead of silently picking the first
one; failures raise a typed, readable error naming the query.

Fetches are done with ``curl`` via subprocess, for the same reliability
reason as ``dlm.network.loader``'s Overpass fetch (see ADR-0002): this
sandboxed environment's outbound networking was found to be more reliable
through curl than through Python's ``requests``/``urllib3`` stack.
"""

from __future__ import annotations

import hashlib
import json
import logging
import subprocess
import time
from pathlib import Path

import osmnx as ox
from pydantic import BaseModel

from dlm.config import settings

logger = logging.getLogger(__name__)

_NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
_USER_AGENT = "dlm-dublin-routing/0.1 (UCD ACM40960 coursework)"
_CURL_MAX_TIME_S = 20
_FETCH_RETRIES = 4
_FETCH_BACKOFF_S = 2.0

# Nominatim usage policy: no more than 1 request/second.
_MIN_REQUEST_INTERVAL_S = 1.0
_last_request_time = 0.0

# A second candidate more than this far from the top one, and at least
# this significant relative to it, counts as a genuinely different place
# (ambiguous), rather than just a less-precise duplicate of the same one.
_AMBIGUITY_DISTANCE_M = 500.0
_AMBIGUITY_IMPORTANCE_RATIO = 0.5


class GeocodeError(ValueError):
    """Raised when a query returns no usable result."""

    def __init__(self, query: str, reason: str) -> None:
        self.query = query
        super().__init__(f"Could not geocode {query!r}: {reason}")


class GeocodeCandidate(BaseModel):
    """One Nominatim search result."""

    display_name: str
    lat: float
    lon: float
    place_type: str | None = None
    importance: float = 0.0


class AmbiguousGeocodeError(GeocodeError):
    """Raised when a query matches multiple genuinely distinct real places.

    Carries the candidates (most important first) so the caller can ask
    the user to pick one, instead of silently guessing.
    """

    def __init__(self, query: str, candidates: list[GeocodeCandidate]) -> None:
        self.candidates = candidates
        preview = "; ".join(f"{c.display_name} ({c.lat:.4f}, {c.lon:.4f})" for c in candidates[:3])
        super().__init__(query, f"{len(candidates)} distinct matches, e.g. {preview}")


class GeocodeResult(BaseModel):
    """A resolved, unambiguous geocode."""

    query: str
    label: str
    lat: float
    lon: float


def _cache_path(query: str, country_codes: str, limit: int) -> Path:
    key = hashlib.sha256(f"{query}|{country_codes}|{limit}".encode()).hexdigest()[:16]
    return settings.cache_dir / "geocode" / f"{key}.json"


def _fetch_raw(query: str, country_codes: str, limit: int) -> list[dict]:
    """Fetch raw Nominatim search results, retrying on transient failures."""
    global _last_request_time  # noqa: PLW0603 - simple module-level rate limiter

    elapsed = time.time() - _last_request_time
    if elapsed < _MIN_REQUEST_INTERVAL_S:
        time.sleep(_MIN_REQUEST_INTERVAL_S - elapsed)

    last_error: Exception | None = None
    for attempt in range(1, _FETCH_RETRIES + 1):
        try:
            result = subprocess.run(  # noqa: S603 - fixed args, no shell
                [
                    "curl",
                    "-sS",
                    "--max-time",
                    str(_CURL_MAX_TIME_S),
                    "-A",
                    _USER_AGENT,
                    "-G",
                    "--data-urlencode",
                    f"q={query}",
                    "--data-urlencode",
                    "format=jsonv2",
                    "--data-urlencode",
                    f"limit={limit}",
                    "--data-urlencode",
                    f"countrycodes={country_codes}",
                    _NOMINATIM_URL,
                ],
                capture_output=True,
                text=True,
                timeout=_CURL_MAX_TIME_S + 5,
                check=False,
            )
            _last_request_time = time.time()
            if result.returncode != 0:
                raise ConnectionError(f"curl exit {result.returncode}: {result.stderr.strip()}")
            return json.loads(result.stdout)
        except (ConnectionError, json.JSONDecodeError, subprocess.TimeoutExpired) as exc:
            last_error = exc
            logger.warning(
                "geocode fetch attempt %d/%d failed for %r (%s); retrying in %.0fs",
                attempt,
                _FETCH_RETRIES,
                query,
                exc,
                _FETCH_BACKOFF_S,
            )
            if attempt < _FETCH_RETRIES:
                time.sleep(_FETCH_BACKOFF_S)
    assert last_error is not None
    raise GeocodeError(query, f"network error after {_FETCH_RETRIES} attempts: {last_error}")


def _get_raw_results(query: str, country_codes: str, limit: int) -> list[dict]:
    """Raw Nominatim results for `query`, from disk cache if present."""
    cache_path = _cache_path(query, country_codes, limit)
    if cache_path.exists():
        return json.loads(cache_path.read_text(encoding="utf-8"))

    raw = _fetch_raw(query, country_codes, limit)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(raw), encoding="utf-8")
    return raw


def geocode(query: str, *, country_codes: str = "ie", limit: int = 5) -> GeocodeResult:
    """Resolve a free-text address/place query to a single lat/lon.

    Parameters
    ----------
    query : str
        Free-text address or place name, e.g. "Trinity College Dublin".
    country_codes : str, default "ie"
        ISO 3166-1 alpha-2 codes to bias/restrict results to (this project
        is Dublin-scoped; restricting to Ireland avoids most cross-country
        ambiguity for common names).
    limit : int, default 5
        Maximum number of candidates to fetch and consider for ambiguity.

    Returns
    -------
    GeocodeResult

    Raises
    ------
    GeocodeError
        No results found, or a network error after retries.
    AmbiguousGeocodeError
        Multiple genuinely distinct places matched; carries `.candidates`.
    """
    raw = _get_raw_results(query, country_codes, limit)
    if not raw:
        raise GeocodeError(query, "no results found")

    candidates = [
        GeocodeCandidate(
            display_name=r["display_name"],
            lat=float(r["lat"]),
            lon=float(r["lon"]),
            place_type=r.get("type"),
            importance=float(r.get("importance", 0.0)),
        )
        for r in raw
    ]
    candidates.sort(key=lambda c: c.importance, reverse=True)

    top = candidates[0]
    distinct_others = [
        c
        for c in candidates[1:]
        if ox.distance.great_circle(top.lat, top.lon, c.lat, c.lon) > _AMBIGUITY_DISTANCE_M
        and c.importance >= top.importance * _AMBIGUITY_IMPORTANCE_RATIO
    ]
    if distinct_others:
        raise AmbiguousGeocodeError(query, [top, *distinct_others])

    return GeocodeResult(query=query, label=top.display_name, lat=top.lat, lon=top.lon)
