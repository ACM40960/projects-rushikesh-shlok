"""T1: cost of the planned route under normal conditions.

`T2`/`T3`/`T3_oracle`/`Saving %` are Stage 6 additions, once a disruption
and a re-optimisation exist to measure. This first version covers exactly
what the brief's Stage 4 acceptance criteria ask for: `T1` reported with a
breakdown (driving time, service time, distance, per-leg table).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from dlm.config import settings
from dlm.instance.schema import Instance
from dlm.solver.base import Solution


@dataclass(frozen=True)
class LegMetric:
    """One leg's contribution to the route, for the per-leg report table."""

    from_id: str
    to_id: str
    travel_time_s: float
    distance_m: float


@dataclass(frozen=True)
class T1Result:
    """`T1`: cost of the planned route under normal conditions.

    Attributes
    ----------
    drive_time_s : float
        Sum of leg travel times — identical to `Solution.total_time_s`,
        duplicated here so `T1Result` is self-contained for reporting.
    service_time_s : float
        Sum of time spent at each visited stop (the depot itself has no
        service time). A stop's own `service_time_s` is used if it is
        non-zero; otherwise `default_service_time_s` (settings, or the
        value passed to `compute_t1`) is assumed — see
        docs/stages/stage-04-baseline.md for why this convention was
        chosen and the open question it's raised as an ADR proposal.
    total_time_s : float
        `drive_time_s + service_time_s` — the headline `T1` number.
    distance_m : float
        Sum of leg distances — identical to `Solution.total_distance_m`.
    n_stops_served : int
        Number of stops visited (always all of them for `T1`; the
        distinction matters from Stage 6, where a disruption can leave
        some unreachable).
    legs : list[LegMetric]
        Per-leg breakdown, depot-to-depot.
    """

    drive_time_s: float
    service_time_s: float
    total_time_s: float
    distance_m: float
    n_stops_served: int
    legs: list[LegMetric] = field(default_factory=list)


def compute_t1(
    instance: Instance,
    solution: Solution,
    default_service_time_s: float | None = None,
) -> T1Result:
    """Compute `T1` for a planned route under normal conditions.

    Parameters
    ----------
    instance : Instance
        Must have the same stops `solution.order` references.
    solution : Solution
        A solved route (e.g. from `TwoOptSolver`).
    default_service_time_s : float, optional
        Fallback per-stop service time when a stop's own is 0 (unset).
        Defaults to `settings.default_service_time_s`.
    """
    fallback = (
        default_service_time_s
        if default_service_time_s is not None
        else settings.default_service_time_s
    )
    stops_by_id = {s.id: s for s in instance.stops}

    service_time_s = sum(
        (stops_by_id[stop_id].service_time_s or fallback) for stop_id in solution.order
    )

    return T1Result(
        drive_time_s=solution.total_time_s,
        service_time_s=service_time_s,
        total_time_s=solution.total_time_s + service_time_s,
        distance_m=solution.total_distance_m,
        n_stops_served=len(solution.order),
        legs=[
            LegMetric(
                from_id=leg.from_id,
                to_id=leg.to_id,
                travel_time_s=leg.travel_time_s,
                distance_m=leg.distance_m,
            )
            for leg in solution.legs
        ],
    )


__all__ = ["LegMetric", "T1Result", "compute_t1"]
