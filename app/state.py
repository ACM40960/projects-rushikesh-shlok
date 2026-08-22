"""Streamlit ``session_state`` helpers, and thin orchestration around
``dlm.*`` for the pages in ``app/main.py``.

Every function below calls the same library functions `dlm.cli`'s
`plan`/`compare`/`instance *` commands call, in the same order, with the
same defaults (`SOLVERS`, the fleet-size branch, the scenario lookup) —
this file adds no routing/solving/disruption/metric logic of its own, per
the architectural law in `docs/architecture.md`. `tests/test_cli_ui_parity.py`
checks this directly: `run_plan`/`run_compare` here and `dlm plan`/
`dlm compare` on the CLI must produce identical `T1`/`T2`/`T3` for the
same inputs.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import networkx as nx

from dlm.config import settings
from dlm.disruption.engine import DisruptionResult, apply_scenario
from dlm.disruption.schema import (
    Disruption,
    DisruptionEffect,
    Scenario,
    find_scenario,
    list_scenarios,
    load_scenario,
)
from dlm.instance.builder import InstanceBuilder, MutationResult
from dlm.instance.matrix import build_matrix
from dlm.instance.presets import load_presets
from dlm.instance.schema import Instance, StopSource
from dlm.network.loader import build_graph
from dlm.simulation.execution import InformationModel
from dlm.simulation.metrics import (
    FleetT1Result,
    T1Result,
    T2Result,
    T3OracleResult,
    T3Result,
    compute_fleet_t1,
    compute_saving,
    compute_t1,
    compute_t2,
    compute_t3,
    compute_t3_oracle,
)
from dlm.solver.base import FleetSolution, Solution
from dlm.solver.clarke_wright import ClarkeWrightSolver
from dlm.solver.nearest_neighbour import NearestNeighbourSolver
from dlm.solver.two_opt import TwoOptSolver

try:
    import streamlit as st
except ImportError:  # pragma: no cover - allows importing this module in tests without streamlit
    st = None  # type: ignore[assignment]

SOLVERS = {"nn_2opt": TwoOptSolver, "nearest_neighbour": NearestNeighbourSolver}

# `st.cache_resource` needs a stable function object to key its cache on;
# defined once at import time rather than inside `get_graph` so repeated
# calls actually hit the cache instead of recomputing every rerun.
_cached_build_graph = (
    st.cache_resource(show_spinner="Loading Dublin road network...")(build_graph)
    if st is not None
    else build_graph
)


def get_graph():  # noqa: ANN201 - tuple[nx.MultiDiGraph, GraphBuildReport], avoid heavy import at module load
    """The same cached graph `dlm network build` builds — memoised for
    the lifetime of the Streamlit session so every widget interaction
    doesn't re-download/re-load it."""
    return _cached_build_graph()


DEFAULT_STATE = {
    "instance_name": None,
    "scenario_name": None,
    "solver_name": "nn_2opt",
    "plan_outcome": None,
    "compare_outcome": None,
}


def init_session_state() -> None:
    """Set every key in `DEFAULT_STATE` that isn't already present. The
    only place `st.session_state` is touched directly outside widget
    callbacks in `app/main.py`."""
    if st is None:
        return
    for key, default in DEFAULT_STATE.items():
        if key not in st.session_state:
            st.session_state[key] = default


def _instance_path(name: str) -> Path:
    return settings.instances_dir / f"{name}.json"


def instance_exists(name: str) -> bool:
    return _instance_path(name).exists()


def list_instance_names() -> list[str]:
    return sorted(p.stem for p in settings.instances_dir.glob("*.json"))


def list_preset_names() -> list[str]:
    return sorted(p.name for p in load_presets())


def list_scenario_names() -> list[str]:
    return sorted(p.stem for p in list_scenarios())


def scenario_has_slow_zone(scenario_name: str) -> bool:
    """Whether a saved scenario contains at least one adjustable slow zone."""
    scenario = load_scenario(find_scenario(scenario_name))
    return any(d.effect is DisruptionEffect.SLOW_ZONE for d in scenario.disruptions)


def scenario_with_speed_factor(scenario_name: str, speed_factor: float) -> Scenario:
    """Return a copy with every slow-zone factor set to ``speed_factor``."""
    if not 0 < speed_factor < 1:
        raise ValueError("speed_factor must be strictly between 0 and 1")
    original = load_scenario(find_scenario(scenario_name))
    adjusted: list[Disruption] = []
    for disruption in original.disruptions:
        values = disruption.model_dump()
        if disruption.effect is DisruptionEffect.SLOW_ZONE:
            values["speed_factor"] = speed_factor
        adjusted.append(Disruption(**values))
    return Scenario(
        schema_version=original.schema_version,
        name=f"{original.name}_adjusted",
        description=(
            f"{original.description} Adjusted remaining-speed factor: {speed_factor:.2f}."
        ),
        source=original.source,
        disruptions=adjusted,
    )


def parse_latlon(value: str) -> tuple[float, float]:
    lat_str, lon_str = value.split(",")
    return float(lat_str.strip()), float(lon_str.strip())


def load_instance(name: str) -> Instance:
    graph, _ = get_graph()
    return InstanceBuilder.load(graph, _instance_path(name)).instance


def instance_rows(instance: Instance) -> list[dict]:
    """Flat depot+stops table for `st.dataframe`."""
    rows = []
    if instance.depot is not None:
        d = instance.depot
        rows.append(
            {
                "role": "depot",
                "id": d.id,
                "label": d.label,
                "lat": d.lat,
                "lon": d.lon,
                "demand": d.demand,
            }
        )
    for s in instance.stops:
        rows.append(
            {
                "role": "stop",
                "id": s.id,
                "label": s.label,
                "lat": s.lat,
                "lon": s.lon,
                "demand": s.demand,
            }
        )
    return rows


def create_instance(
    name: str,
    depot_kind: str,
    depot_value: str,
    fleet_size: int = 1,
    vehicle_capacity: float | None = None,
    seed: int = 42,
    force: bool = False,
) -> MutationResult:
    path = _instance_path(name)
    if path.exists() and not force:
        raise FileExistsError(f"Instance {name!r} already exists at {path}.")

    graph, _ = get_graph()
    builder = InstanceBuilder(
        graph, name=name, seed=seed, fleet_size=fleet_size, vehicle_capacity=vehicle_capacity
    )
    if depot_kind == "preset":
        result = builder.set_depot_from_preset(depot_value)
    elif depot_kind == "address":
        result = builder.set_depot_from_address(depot_value)
    elif depot_kind == "latlon":
        lat, lon = parse_latlon(depot_value)
        result = builder.set_depot_from_latlon(lat, lon)
    else:
        raise ValueError(f"Unknown depot_kind {depot_kind!r}. Choices: preset, address, latlon.")

    builder.save(path)
    return result


def add_stop(
    name: str,
    kind: str,
    value: str,
    label: str | None = None,
    demand: float = 0.0,
) -> MutationResult:
    """`kind` is one of `preset` / `address` / `latlon` / `map_click`
    (the latter is what a folium map-click handler passes — same
    mechanics as `latlon`, tagged with a different `StopSource` so a
    saved instance records how each stop was actually chosen)."""
    graph, _ = get_graph()
    builder = InstanceBuilder.load(graph, _instance_path(name))
    if kind == "preset":
        result = builder.add_stop_from_preset(value, demand=demand)
    elif kind == "address":
        result = builder.add_stop_from_address(value, label=label, demand=demand)
    elif kind == "latlon":
        lat, lon = parse_latlon(value)
        result = builder.add_stop_from_latlon(lat, lon, label=label, demand=demand)
    elif kind == "map_click":
        lat, lon = parse_latlon(value)
        result = builder.add_stop_from_latlon(
            lat, lon, label=label, source=StopSource.MAP_CLICK, demand=demand
        )
    else:
        raise ValueError(f"Unknown kind {kind!r}. Choices: preset, address, latlon, map_click.")

    builder.save(_instance_path(name))
    return result


def add_random_stops(name: str, n: int, seed: int = 42) -> list[MutationResult]:
    graph, _ = get_graph()
    builder = InstanceBuilder.load(graph, _instance_path(name))
    results = builder.add_random_stops(n, seed=seed)
    builder.save(_instance_path(name))
    return results


def remove_stop(name: str, stop_id: str) -> MutationResult:
    graph, _ = get_graph()
    builder = InstanceBuilder.load(graph, _instance_path(name))
    result = builder.remove_stop(stop_id)
    builder.save(_instance_path(name))
    return result


@dataclass
class PlanOutcome:
    """Everything the plan section of the UI needs to render: the built
    instance/graph (for the map), which solver ran, and its `T1` (or
    `FleetT1Result` for `fleet_size > 1`)."""

    instance: Instance
    graph: nx.MultiDiGraph
    solver_name: str
    solution: Solution | None
    fleet: FleetSolution | None
    t1: T1Result | FleetT1Result


def run_plan(instance_name: str, solver_name: str = "nn_2opt") -> PlanOutcome:
    """Solve an instance's baseline route and compute `T1` — identical
    call sequence to `dlm.cli.plan`/`dlm.cli._plan_fleet`."""
    graph, graph_report = get_graph()
    inst = InstanceBuilder.load(graph, _instance_path(instance_name)).build()
    nodes = [inst.depot.node, *(s.node for s in inst.stops)]
    matrix, _ = build_matrix(graph, nodes, graph_id=graph_report.cache_path.stem)

    if inst.fleet_size > 1:
        fleet = ClarkeWrightSolver().solve_fleet(inst, matrix)
        t1 = compute_fleet_t1(inst, fleet)
        return PlanOutcome(inst, graph, "clarke_wright_2opt", None, fleet, t1)

    if solver_name not in SOLVERS:
        raise ValueError(f"Unknown solver {solver_name!r}. Choices: {', '.join(SOLVERS)}")
    solution = SOLVERS[solver_name]().solve(inst, matrix)
    t1 = compute_t1(inst, solution)
    return PlanOutcome(inst, graph, solver_name, solution, None, t1)


@dataclass
class CompareOutcome:
    """Everything the disruption/compare section of the UI needs: the
    `T1` plan, the disruption's effect on the graph, and `T2`
    (both information models)/`T3`/`T3_oracle`/`Saving %`."""

    instance: Instance
    graph: nx.MultiDiGraph
    solution: Solution
    disruption_result: DisruptionResult
    t1: T1Result
    t2_omniscient: T2Result
    t2_reactive: T2Result
    t3: T3Result
    t3_oracle: T3OracleResult
    saving_pct: float | None


def run_compare(
    instance_name: str,
    scenario_name: str,
    solver_name: str = "nn_2opt",
    at_time: float | None = 0.0,
    scenario_override: Scenario | None = None,
) -> CompareOutcome:
    """`T1`/`T2`/`T3`/`T3_oracle`/`Saving %` for an instance under a
    scenario — identical call sequence to `dlm.cli.compare`.

    Single-vehicle only (`fleet_size == 1`), same as the CLI: no
    fleet-aware `T2`/`T3` exists yet (Stage 8's documented limitation).
    """
    graph, graph_report = get_graph()
    inst = InstanceBuilder.load(graph, _instance_path(instance_name)).build()
    if inst.fleet_size > 1:
        raise ValueError(
            f"Instance {instance_name!r} has fleet_size={inst.fleet_size} > 1 — disruption "
            "comparison only supports single-vehicle instances (see docs/limitations.md)."
        )

    scenario = (
        scenario_override
        if scenario_override is not None
        else load_scenario(find_scenario(scenario_name))
    )

    nodes = [inst.depot.node, *(s.node for s in inst.stops)]
    matrix, _ = build_matrix(graph, nodes, graph_id=graph_report.cache_path.stem)
    if solver_name not in SOLVERS:
        raise ValueError(f"Unknown solver {solver_name!r}. Choices: {', '.join(SOLVERS)}")
    solution = SOLVERS[solver_name]().solve(inst, matrix)
    t1 = compute_t1(inst, solution)

    disruption_result = apply_scenario(graph, scenario, at_time=at_time)
    t2_omniscient = compute_t2(inst, solution, disruption_result.graph, InformationModel.OMNISCIENT)
    t2_reactive = compute_t2(inst, solution, disruption_result.graph, InformationModel.REACTIVE)
    t3 = compute_t3(inst, solution, disruption_result.graph)
    t3_oracle = compute_t3_oracle(inst, disruption_result.graph)
    saving_pct = compute_saving(t2_reactive, t3)

    return CompareOutcome(
        instance=inst,
        graph=graph,
        solution=solution,
        disruption_result=disruption_result,
        t1=t1,
        t2_omniscient=t2_omniscient,
        t2_reactive=t2_reactive,
        t3=t3,
        t3_oracle=t3_oracle,
        saving_pct=saving_pct,
    )


__all__ = [
    "CompareOutcome",
    "DEFAULT_STATE",
    "PlanOutcome",
    "SOLVERS",
    "add_random_stops",
    "add_stop",
    "create_instance",
    "get_graph",
    "init_session_state",
    "instance_exists",
    "instance_rows",
    "list_instance_names",
    "list_preset_names",
    "list_scenario_names",
    "load_instance",
    "parse_latlon",
    "remove_stop",
    "run_compare",
    "run_plan",
    "scenario_has_slow_zone",
    "scenario_with_speed_factor",
]
