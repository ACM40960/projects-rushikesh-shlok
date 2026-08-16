"""``Disruption`` and ``Scenario`` models: edge/node/corridor/polygon
closures and slow zones, with optional time windows and severity.

A ``Disruption`` is described by two independent choices:

- **shape** — *what part of the graph* it targets: a single ``edge``, a
  ``node`` (and everything incident to it), a ``corridor`` (an ordered
  street-following path between waypoints), or a ``polygon`` (an area).
- **effect** — *what happens* to the edges that shape resolves to: a
  ``closure`` (removed entirely — the street is impassable) or a
  ``slow_zone`` (travel time scaled up by ``1 / speed_factor`` — the
  street is still usable, just slower).

Any shape can carry either effect (a ``polygon`` + ``slow_zone`` models a
whole neighbourhood under roadworks; a ``polygon`` + ``closure`` models a
cordoned-off area). Geometry (lat/lon -> graph node/edge) is resolved
against a specific graph at *application* time
(:func:`dlm.disruption.engine.apply_scenario`), not at parse time — a
``Scenario`` file has no graph to resolve against until then.

A ``Scenario`` is a named, citable list of ``Disruption``\\ s — see
``scenarios/README.md`` for the on-disk YAML layout and a worked example.
"""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, model_validator

from dlm.config import settings

SCHEMA_VERSION = 1

LatLon = tuple[float, float]
"""(lat, lon) in WGS84 decimal degrees — the project's usual convention
(``dlm.config``), not GeoJSON's (lon, lat)."""


class DisruptionShape(StrEnum):
    """What part of the graph a disruption targets."""

    EDGE = "edge"
    NODE = "node"
    CORRIDOR = "corridor"
    POLYGON = "polygon"


class DisruptionEffect(StrEnum):
    """What happens to the edges a disruption's shape resolves to."""

    CLOSURE = "closure"
    SLOW_ZONE = "slow_zone"


class Disruption(BaseModel):
    """One named change to the graph. See the module docstring for the
    shape x effect design.

    Attributes
    ----------
    id : str
        Unique within its ``Scenario``. Referenced by
        :class:`dlm.disruption.engine.EdgeChange` so an audit can be traced
        back to the disruption that caused it.
    shape : DisruptionShape
    effect : DisruptionEffect
    description, citation : str, optional
        Human-readable context; ``citation`` records a source for curated
        library scenarios (a news report, a council notice), left empty for
        user-authored ones.
    from_node/to_node, from_latlon/to_latlon : shape=EDGE
        Either the two endpoint node ids, or two lat/lon points to be
        snapped at application time. Exactly one pair.
    directions : shape=EDGE
        Which direction(s) of the edge to affect. Closures/slow zones on
        node/corridor/polygon shapes always affect every direction found —
        a blocked junction or cordoned street blocks all approaches, so a
        per-direction choice would be a distinction without a realistic
        difference.
    node, at : shape=NODE
        Either the node id, or a lat/lon to be snapped. Exactly one.
    waypoints : shape=CORRIDOR
        Ordered lat/lon points (>= 2). Consecutive points are connected by
        the *shortest path between their snapped nodes on the undisrupted
        graph* — this is what lets a handful of waypoints describe a real
        street's worth of edges without enumerating every one.
    boundary : shape=POLYGON
        Lat/lon points (>= 3) forming a ring; closed automatically if the
        first and last point differ.
    speed_factor : effect=SLOW_ZONE
        Fraction of normal speed still achievable, in (0, 1) exclusive —
        e.g. 0.5 halves the speed (doubles travel time). Required for
        ``slow_zone``, must be unset for ``closure`` (a closure has no
        partial speed; use ``slow_zone`` for that).
    time_window : (start_s, end_s), optional
        Seconds from scenario start. ``None`` means active for the whole
        run. Unused until Stage 6/7 apply a scenario at a specific
        simulated time.
    severity : float, default 1.0
        Informational weight in [0, 1] (how severe/likely this disruption
        is) — read by Stage 7's generators for sampling, not by the engine;
        the engine's cost effect is fully determined by ``effect`` and
        ``speed_factor``.
    """

    id: str
    shape: DisruptionShape
    effect: DisruptionEffect
    description: str = ""
    citation: str | None = None

    from_node: int | None = None
    to_node: int | None = None
    from_latlon: LatLon | None = None
    to_latlon: LatLon | None = None
    directions: Literal["both", "forward", "reverse"] = "both"

    node: int | None = None
    at: LatLon | None = None

    waypoints: list[LatLon] | None = None

    boundary: list[LatLon] | None = None

    speed_factor: float | None = Field(default=None, gt=0.0, lt=1.0)

    time_window: tuple[float, float] | None = None
    severity: float = Field(default=1.0, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def _check_shape_geometry(self) -> Disruption:
        if self.shape is DisruptionShape.EDGE:
            by_node = self.from_node is not None and self.to_node is not None
            by_latlon = self.from_latlon is not None and self.to_latlon is not None
            if by_node == by_latlon:  # neither, or both
                raise ValueError(
                    f"disruption {self.id!r}: shape=edge needs exactly one of "
                    "(from_node, to_node) or (from_latlon, to_latlon)"
                )
        elif self.shape is DisruptionShape.NODE:
            if (self.node is None) == (self.at is None):
                raise ValueError(
                    f"disruption {self.id!r}: shape=node needs exactly one of node or at"
                )
        elif self.shape is DisruptionShape.CORRIDOR:
            if self.waypoints is None or len(self.waypoints) < 2:
                raise ValueError(f"disruption {self.id!r}: shape=corridor needs >= 2 waypoints")
        elif self.shape is DisruptionShape.POLYGON:
            if self.boundary is None or len(self.boundary) < 3:
                raise ValueError(
                    f"disruption {self.id!r}: shape=polygon needs >= 3 boundary points"
                )
        return self

    @model_validator(mode="after")
    def _check_effect_fields(self) -> Disruption:
        if self.effect is DisruptionEffect.SLOW_ZONE and self.speed_factor is None:
            raise ValueError(
                f"disruption {self.id!r}: effect=slow_zone requires speed_factor in (0, 1)"
            )
        if self.effect is DisruptionEffect.CLOSURE and self.speed_factor is not None:
            raise ValueError(
                f"disruption {self.id!r}: effect=closure must not set speed_factor "
                "(use effect=slow_zone for a partial speed reduction)"
            )
        return self

    @model_validator(mode="after")
    def _check_time_window(self) -> Disruption:
        if self.time_window is not None and self.time_window[0] >= self.time_window[1]:
            raise ValueError(
                f"disruption {self.id!r}: time_window start ({self.time_window[0]}) "
                f"must be before end ({self.time_window[1]})"
            )
        return self


class Scenario(BaseModel):
    """A named, citable list of :class:`Disruption`\\ s — the unit
    :func:`dlm.disruption.engine.apply_scenario` operates on.
    """

    schema_version: int = SCHEMA_VERSION
    name: str
    description: str = ""
    source: str | None = None
    disruptions: list[Disruption] = Field(min_length=1)

    @model_validator(mode="after")
    def _check_unique_ids(self) -> Scenario:
        ids = [d.id for d in self.disruptions]
        if len(ids) != len(set(ids)):
            dupes = sorted({i for i in ids if ids.count(i) > 1})
            raise ValueError(f"duplicate disruption ids in scenario {self.name!r}: {dupes}")
        return self


class ScenarioNotFoundError(KeyError):
    """Raised when a scenario name has no matching YAML file."""

    def __init__(self, name: str, available: list[str]) -> None:
        self.name = name
        self.available = available
        super().__init__(
            f"No scenario named {name!r}. Available: {', '.join(sorted(available)) or '(none)'}"
        )


def _scenarios_dirs() -> list[Path]:
    """Where scenario YAML files live: the top-level scenarios directory
    and its curated ``library/`` subfolder, searched recursively."""
    return [settings.scenarios_dir]


def load_scenario(path: Path) -> Scenario:
    """Load and validate a single scenario YAML file."""
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    return Scenario(**raw)


def list_scenarios() -> list[Path]:
    """All scenario YAML files under ``scenarios/`` (recursive), sorted."""
    paths: list[Path] = []
    for base in _scenarios_dirs():
        if base.exists():
            paths.extend(base.rglob("*.yaml"))
    return sorted(paths)


def find_scenario(name: str) -> Path:
    """Find a scenario YAML file by its filename stem (case-insensitive).

    Raises
    ------
    ScenarioNotFoundError
        If no scenario file's stem matches `name`.
    """
    for path in list_scenarios():
        if path.stem.lower() == name.lower():
            return path
    raise ScenarioNotFoundError(name, [p.stem for p in list_scenarios()])
