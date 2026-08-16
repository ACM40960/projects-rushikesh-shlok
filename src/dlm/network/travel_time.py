"""Edge speed imputation and travel-time computation.

For every edge in a routable graph, this module assigns a `speed_kph` (from
OSM `maxspeed` where a usable value is present, else from a per-`highway`-type
default table for Ireland) and a `travel_time` in **seconds**
(`travel_time = length_m / speed_m_s`).

The default speed table lives in ``speed_defaults.yaml`` next to this module
(not hard-coded here), so it can be inspected, cited, and edited without
touching code. See ``docs/data.md`` for the table's basis and citation.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import networkx as nx
import yaml

logger = logging.getLogger(__name__)

_SPEED_DEFAULTS_PATH = Path(__file__).with_name("speed_defaults.yaml")

_MPH_RE = re.compile(r"^\s*([\d.]+)\s*mph\s*$", re.IGNORECASE)
_KPH_RE = re.compile(r"^\s*([\d.]+)\s*(?:km/?h)?\s*$", re.IGNORECASE)
_MPH_TO_KPH = 1.609344


@dataclass(frozen=True)
class TravelTimeStats:
    """Coverage report for a graph's speed/travel-time assignment.

    Attributes
    ----------
    n_edges : int
        Total number of edges processed.
    n_real_maxspeed : int
        Edges whose speed came from a usable OSM `maxspeed` tag.
    n_imputed : int
        Edges whose speed came from the highway-type default table.
    """

    n_edges: int
    n_real_maxspeed: int
    n_imputed: int

    @property
    def pct_real(self) -> float:
        """Percentage of edges with a real (non-imputed) `maxspeed`."""
        if self.n_edges == 0:
            return 0.0
        return 100.0 * self.n_real_maxspeed / self.n_edges


def load_speed_defaults(path: Path | None = None) -> dict[str, float]:
    """Load the per-`highway`-type default speed table (km/h).

    Parameters
    ----------
    path : Path, optional
        Defaults to ``speed_defaults.yaml`` next to this module.

    Returns
    -------
    dict[str, float]
        Maps an OSM `highway` value (or ``"default"``) to a speed in km/h.
    """
    resolved = path or _SPEED_DEFAULTS_PATH
    with resolved.open(encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    return {str(k): float(v) for k, v in raw.items() if not str(k).startswith("#")}


def _first_tag(value: Any) -> Any:
    """OSM tags are sometimes a list (multiple values on one way); take the first."""
    if isinstance(value, list):
        return value[0] if value else None
    return value


def _parse_maxspeed_kph(maxspeed: Any) -> float | None:
    """Parse an OSM `maxspeed` tag value into km/h, or None if unusable.

    Handles plain numbers (assumed km/h), "<n> mph", and lists (first
    parseable value wins). Non-numeric values such as "national",
    "signals", or "none" are treated as unusable.
    """
    value = _first_tag(maxspeed)
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None

    mph_match = _MPH_RE.match(text)
    if mph_match:
        return float(mph_match.group(1)) * _MPH_TO_KPH

    kph_match = _KPH_RE.match(text)
    if kph_match:
        return float(kph_match.group(1))

    return None


def _default_speed_kph(highway: Any, defaults: dict[str, float]) -> float:
    """Look up the default speed for an edge's `highway` tag."""
    value = _first_tag(highway)
    key = str(value) if value is not None else "default"
    return defaults.get(key, defaults["default"])


def add_travel_times(
    G: nx.MultiDiGraph,
    speed_defaults: dict[str, float] | None = None,
) -> tuple[nx.MultiDiGraph, TravelTimeStats]:
    """Assign `speed_kph`, `speed_source`, and `travel_time` (s) to every edge.

    Parameters
    ----------
    G : nx.MultiDiGraph
        A routable graph with `length` (metres) and `highway` edge attributes,
        as produced by OSMnx.
    speed_defaults : dict[str, float], optional
        Per-`highway`-type default speeds in km/h. Defaults to
        :func:`load_speed_defaults`.

    Returns
    -------
    (nx.MultiDiGraph, TravelTimeStats)
        The same graph object, mutated in place, and a coverage report.

    Notes
    -----
    `speed_source` is either ``"osm_maxspeed"`` or ``"imputed"`` on every
    edge, so downstream code (and the report) can always tell which edges
    are trusted OSM data versus a modelling assumption.
    """
    defaults = speed_defaults or load_speed_defaults()

    n_real = 0
    n_imputed = 0
    for _u, _v, _k, data in G.edges(keys=True, data=True):
        speed_kph = _parse_maxspeed_kph(data.get("maxspeed"))
        if speed_kph is not None and speed_kph > 0:
            data["speed_kph"] = speed_kph
            data["speed_source"] = "osm_maxspeed"
            n_real += 1
        else:
            speed_kph = _default_speed_kph(data.get("highway"), defaults)
            data["speed_kph"] = speed_kph
            data["speed_source"] = "imputed"
            n_imputed += 1

        length_m = float(data["length"])
        speed_m_s = speed_kph * 1000.0 / 3600.0
        data["travel_time"] = length_m / speed_m_s

    stats = TravelTimeStats(
        n_edges=G.number_of_edges(),
        n_real_maxspeed=n_real,
        n_imputed=n_imputed,
    )
    logger.info(
        "travel_time assigned: %d edges, %.1f%% real maxspeed, %d imputed",
        stats.n_edges,
        stats.pct_real,
        stats.n_imputed,
    )
    return G, stats
