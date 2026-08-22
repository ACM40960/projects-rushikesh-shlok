"""Instance data models: ``Stop``, ``Depot``, ``Instance``.

``Instance.n_stops`` is always a property derived from ``len(stops)`` —
never a stored constant — per the project's dynamic-``N`` requirement
(brief §1.1): no module, cache key, or downstream code may assume a fixed
number of stops.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, Field, field_validator

MIN_STOPS = 1
MAX_STOPS = 50
SCHEMA_VERSION = 1


class StopSource(StrEnum):
    """How a stop's location was chosen — kept so instances are self-documenting."""

    ADDRESS = "address"
    LATLON = "latlon"
    PRESET = "preset"
    RANDOM = "random"
    MAP_CLICK = "map_click"


class Stop(BaseModel):
    """A single delivery location, or (as ``Depot``) the vehicle's base.

    Attributes
    ----------
    id : str
        Stable identifier within an instance, e.g. ``"s1"``. Assigned once
        when the stop is added and never reused within the same builder
        session, so a stop keeps its id across later mutations to *other*
        stops.
    label : str
        Human-readable name, e.g. "Trinity College Dublin".
    lat, lon : float
        WGS84 decimal degrees.
    node : int
        The graph node this point snapped to (see
        ``dlm.network.snapping.snap_to_node``).
    demand : float
        Delivery demand/quantity at this stop. Unused until Stage 8
        (capacitated routing); present now so the field never needs to be
        retrofitted onto saved instances later.
    service_time_s : float
        Time spent at the stop, in seconds. Unused in cost calculations
        until Stage 4; present now for the same forward-compatibility reason.
    time_window : tuple[float, float] | None
        ``(earliest_s, latest_s)`` from route start. Unused until Stage 8
        (VRPTW).
    source : StopSource
        How this stop's coordinates were chosen.
    """

    id: str
    label: str
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    node: int
    demand: float = Field(default=0.0, ge=0.0)
    service_time_s: float = Field(default=0.0, ge=0.0)
    time_window: tuple[float, float] | None = None
    source: StopSource

    @field_validator("time_window")
    @classmethod
    def _valid_time_window(cls, value: tuple[float, float] | None) -> tuple[float, float] | None:
        if value is None:
            return value
        start, end = value
        if start < 0 or end < 0:
            raise ValueError("time-window bounds must be non-negative")
        if end < start:
            raise ValueError("time-window end must be greater than or equal to its start")
        return value


class Depot(Stop):
    """The vehicle fleet's start/end point — same shape as a Stop, chosen
    the same way (address / lat-lon / preset / random / map-click), but
    conceptually singular: an instance has exactly one depot.
    """


class InstanceValidationError(ValueError):
    """Raised by :meth:`InstanceBuilder.build` when an instance is not
    ready for use. Carries every problem found, not just the first, so a
    caller can report them all at once."""

    def __init__(self, errors: list[str]) -> None:
        self.errors = errors
        super().__init__("; ".join(errors))


class Instance(BaseModel):
    """A depot + a set of ``N`` stops (+ fleet size ``K``) — the unit a
    solver operates on.

    ``N`` (see :attr:`n_stops`) is always computed from ``len(stops)``.
    This model itself does not enforce the ``1 <= N <= 50`` business rule
    or cross-stop checks (reachability, duplicate nodes) at construction
    time, because it is also the shape used to persist an *in-progress*
    instance being built interactively (which may transiently have zero
    stops, e.g. right after setting only the depot). Those checks run in
    :meth:`InstanceBuilder.build`.
    """

    schema_version: int = SCHEMA_VERSION
    name: str
    depot: Depot | None = None
    stops: list[Stop] = Field(default_factory=list)
    fleet_size: int = Field(default=1, ge=1)
    vehicle_capacity: float | None = Field(default=None, gt=0.0)
    seed: int = 42
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator("name")
    @classmethod
    def _name_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("instance name must not be empty")
        return v

    @property
    def n_stops(self) -> int:
        """Number of stops. Always derived — never cache this value."""
        return len(self.stops)
