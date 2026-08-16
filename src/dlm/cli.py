"""``dlm`` — the command-line entry point for the whole pipeline.

The architectural rule (§2.1 of the project brief) is that the UI is a thin
client: every capability the Streamlit app exposes must already exist as a
CLI command here. Sub-commands are added stage by stage as their underlying
modules land:

- Stage 1 adds ``dlm network build`` / ``dlm network stats``.
- Stage 2 adds ``dlm instance new`` / ``add`` / ``remove`` / ``move`` / ``rename`` /
  ``random`` / ``list`` / ``show`` / ``map``.
- Stage 3 adds ``dlm instance matrix``.
- Stage 4 adds ``dlm plan``.
- Stage 5 adds ``dlm disrupt list`` / ``validate`` / ``preview`` / ``new``.
- Stage 6 adds ``dlm compare``.
- Stage 7 adds ``dlm batch``, ``dlm figures``, ``dlm sensitivity``.
- Stage 8 adds ``dlm plan`` fleet support (``fleet_size > 1``) and
  ``dlm benchmark``.

This file intentionally has no domain logic — every command is a thin
wrapper that parses arguments and calls into ``dlm.network`` / ``dlm.instance``
/ ``dlm.solver`` / ``dlm.disruption`` / ``dlm.simulation``.
"""

from __future__ import annotations

from pathlib import Path

import typer

from dlm import __version__
from dlm.logging_conf import configure_logging

app = typer.Typer(
    name="dlm",
    help="Disruption-aware last-mile routing on Dublin's road network.",
    no_args_is_help=True,
)

network_app = typer.Typer(help="Build and inspect the Dublin road network graph.")
app.add_typer(network_app, name="network")

instance_app = typer.Typer(help="Build and inspect delivery instances (depot + N stops).")
app.add_typer(instance_app, name="instance")

disrupt_app = typer.Typer(help="Author, validate, and preview disruption scenarios.")
app.add_typer(disrupt_app, name="disrupt")


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"dlm {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: bool = typer.Option(
        False,
        "--version",
        callback=_version_callback,
        is_eager=True,
        help="Show the version and exit.",
    ),
) -> None:
    """Disruption-aware last-mile routing on Dublin's road network."""
    configure_logging()


def _report_lines(report) -> list[str]:  # noqa: ANN001 - GraphBuildReport, avoid import at module load
    stats = report.travel_time_stats
    dropped_nodes = report.n_nodes_before_scc - report.n_nodes
    dropped_edges = report.n_edges_before_scc - report.n_edges
    return [
        f"cache:            {report.cache_path} ({'hit' if report.from_cache else 'built fresh'})",
        f"build time:       {report.build_seconds:.2f}s",
        f"nodes:            {report.n_nodes} (dropped {dropped_nodes} outside largest strongly "
        "connected component)",
        f"edges:            {report.n_edges} (dropped {dropped_edges})",
        f"maxspeed real:    {stats.n_real_maxspeed}/{stats.n_edges} ({stats.pct_real:.1f}%)",
        f"maxspeed imputed: {stats.n_imputed}/{stats.n_edges}",
    ]


@network_app.command("build")
def network_build(
    force: bool = typer.Option(False, "--force", help="Ignore the cache and re-download."),
) -> None:
    """Download (or load from cache) the Dublin routable graph and report its stats."""
    from dlm.network.loader import build_graph

    _, report = build_graph(force_rebuild=force)
    for line in _report_lines(report):
        typer.echo(line)


@network_app.command("stats")
def network_stats() -> None:
    """Print stats for the cached Dublin graph, building it first if needed."""
    from dlm.network.loader import build_graph

    _, report = build_graph()
    for line in _report_lines(report):
        typer.echo(line)


def _parse_latlon(value: str) -> tuple[float, float]:
    try:
        lat_str, lon_str = value.split(",")
        return float(lat_str.strip()), float(lon_str.strip())
    except ValueError as exc:
        raise typer.BadParameter(
            f"expected 'lat,lon' (e.g. '53.3438,-6.2546'), got {value!r}"
        ) from exc


def _instance_path(name: str):  # noqa: ANN201 - Path, avoid import at module load
    from dlm.config import settings

    return settings.instances_dir / f"{name}.json"


def _load_builder(name: str):  # noqa: ANN201 - InstanceBuilder, avoid import at module load
    from dlm.instance.builder import InstanceBuilder
    from dlm.network.loader import build_graph

    path = _instance_path(name)
    if not path.exists():
        typer.echo(
            f"No instance named {name!r} (looked in {path}). Create one with `dlm instance new`."
        )
        raise typer.Exit(code=1)
    graph, _ = build_graph()
    return InstanceBuilder.load(graph, path)


def _report_mutation(result) -> None:  # noqa: ANN001 - MutationResult, avoid import at module load
    typer.echo(result.message)


def _report_geocode_error(exc: Exception) -> None:
    from dlm.instance.geocode import AmbiguousGeocodeError

    if isinstance(exc, AmbiguousGeocodeError):
        typer.echo(f"Ambiguous address: {exc}")
        typer.echo("Candidates:")
        for c in exc.candidates:
            typer.echo(f"  - {c.display_name}  ({c.lat:.5f}, {c.lon:.5f})")
        typer.echo("Try a more specific query, or use --latlon with one of the candidates above.")
    else:
        typer.echo(f"Error: {exc}")
    raise typer.Exit(code=1)


@instance_app.command("new")
def instance_new(
    name: str = typer.Option(..., "--name", help="Instance name (used as the save filename)."),
    depot_address: str | None = typer.Option(None, "--depot-address"),
    depot_latlon: str | None = typer.Option(None, "--depot-latlon", help="'lat,lon'"),
    depot_preset: str | None = typer.Option(None, "--depot-preset"),
    seed: int = typer.Option(42, "--seed"),
    fleet_size: int = typer.Option(1, "--fleet-size", min=1, help="Number of vehicles, K."),
    vehicle_capacity: float | None = typer.Option(
        None, "--vehicle-capacity", help="Per-vehicle demand capacity (unset = unlimited)."
    ),
    force: bool = typer.Option(
        False, "--force", help="Overwrite an existing instance of this name."
    ),
) -> None:
    """Create a new instance with its depot. Exactly one --depot-* flag is required."""
    from dlm.instance.builder import InstanceBuilder
    from dlm.instance.geocode import GeocodeError
    from dlm.instance.presets import PresetNotFoundError
    from dlm.network.loader import build_graph
    from dlm.network.snapping import SnapError

    depot_flags = [f for f in (depot_address, depot_latlon, depot_preset) if f is not None]
    if len(depot_flags) != 1:
        typer.echo("Exactly one of --depot-address / --depot-latlon / --depot-preset is required.")
        raise typer.Exit(code=1)

    path = _instance_path(name)
    if path.exists() and not force:
        typer.echo(f"Instance {name!r} already exists at {path}. Use --force to overwrite.")
        raise typer.Exit(code=1)

    graph, _ = build_graph()
    builder = InstanceBuilder(
        graph,
        name=name,
        seed=seed,
        fleet_size=fleet_size,
        vehicle_capacity=vehicle_capacity,
    )
    try:
        if depot_address is not None:
            result = builder.set_depot_from_address(depot_address)
        elif depot_latlon is not None:
            lat, lon = _parse_latlon(depot_latlon)
            result = builder.set_depot_from_latlon(lat, lon)
        else:
            assert depot_preset is not None
            result = builder.set_depot_from_preset(depot_preset)
    except (SnapError, PresetNotFoundError) as exc:
        typer.echo(f"Error: {exc}")
        raise typer.Exit(code=1) from exc
    except GeocodeError as exc:
        _report_geocode_error(exc)

    builder.save(path)
    _report_mutation(result)
    typer.echo(f"Saved to {path}")


@instance_app.command("add")
def instance_add(
    name: str = typer.Option(..., "--name"),
    address: str | None = typer.Option(None, "--address"),
    latlon: str | None = typer.Option(None, "--latlon", help="'lat,lon'"),
    preset: str | None = typer.Option(None, "--preset"),
    label: str | None = typer.Option(None, "--label"),
    demand: float = typer.Option(0.0, "--demand", help="Delivery demand at this stop (CVRP)."),
) -> None:
    """Add one stop by address, lat/lon, or preset. Exactly one is required."""
    from dlm.instance.geocode import GeocodeError
    from dlm.instance.presets import PresetNotFoundError
    from dlm.network.snapping import SnapError

    flags = [f for f in (address, latlon, preset) if f is not None]
    if len(flags) != 1:
        typer.echo("Exactly one of --address / --latlon / --preset is required.")
        raise typer.Exit(code=1)

    builder = _load_builder(name)
    try:
        if address is not None:
            result = builder.add_stop_from_address(address, label=label, demand=demand)
        elif latlon is not None:
            lat, lon = _parse_latlon(latlon)
            result = builder.add_stop_from_latlon(lat, lon, label=label, demand=demand)
        else:
            assert preset is not None
            result = builder.add_stop_from_preset(preset, demand=demand)
    except (SnapError, PresetNotFoundError) as exc:
        typer.echo(f"Error: {exc}")
        raise typer.Exit(code=1) from exc
    except GeocodeError as exc:
        _report_geocode_error(exc)

    builder.save(_instance_path(name))
    _report_mutation(result)


@instance_app.command("random")
def instance_random(
    name: str = typer.Option(..., "--name"),
    n: int = typer.Option(..., "--n", min=1),
    seed: int = typer.Option(42, "--seed"),
) -> None:
    """Add `n` seeded-random stops drawn from routable graph nodes."""
    builder = _load_builder(name)
    results = builder.add_random_stops(n, seed=seed)
    builder.save(_instance_path(name))
    for r in results:
        _report_mutation(r)


@instance_app.command("remove")
def instance_remove(
    name: str = typer.Option(..., "--name"),
    stop: str = typer.Option(..., "--stop"),
) -> None:
    """Remove a stop by id (see `dlm instance show`)."""
    builder = _load_builder(name)
    try:
        result = builder.remove_stop(stop)
    except KeyError as exc:
        typer.echo(f"Error: {exc}")
        raise typer.Exit(code=1) from exc
    builder.save(_instance_path(name))
    _report_mutation(result)


@instance_app.command("move")
def instance_move(
    name: str = typer.Option(..., "--name"),
    stop: str = typer.Option(..., "--stop"),
    latlon: str = typer.Option(..., "--latlon", help="'lat,lon'"),
) -> None:
    """Move an existing stop to a new lat/lon (re-snaps)."""
    from dlm.network.snapping import SnapError

    builder = _load_builder(name)
    lat, lon = _parse_latlon(latlon)
    try:
        result = builder.move_stop(stop, lat, lon)
    except (KeyError, SnapError) as exc:
        typer.echo(f"Error: {exc}")
        raise typer.Exit(code=1) from exc
    builder.save(_instance_path(name))
    _report_mutation(result)


@instance_app.command("rename")
def instance_rename(
    name: str = typer.Option(..., "--name"),
    stop: str = typer.Option(..., "--stop"),
    label: str = typer.Option(..., "--label"),
) -> None:
    """Rename an existing stop."""
    builder = _load_builder(name)
    try:
        result = builder.rename_stop(stop, label)
    except KeyError as exc:
        typer.echo(f"Error: {exc}")
        raise typer.Exit(code=1) from exc
    builder.save(_instance_path(name))
    _report_mutation(result)


@instance_app.command("list")
def instance_list() -> None:
    """List all saved instances."""
    from dlm.config import settings

    paths = sorted(settings.instances_dir.glob("*.json"))
    if not paths:
        typer.echo(f"No instances saved yet in {settings.instances_dir}.")
        return
    for path in paths:
        import json

        data = json.loads(path.read_text(encoding="utf-8"))
        depot_label = data["depot"]["label"] if data.get("depot") else "(no depot)"
        typer.echo(f"{data['name']}: {len(data['stops'])} stops, depot={depot_label}")


@instance_app.command("show")
def instance_show(name: str = typer.Option(..., "--name")) -> None:
    """Show full detail for one saved instance."""
    builder = _load_builder(name)
    inst = builder.instance
    typer.echo(f"name:          {inst.name}")
    typer.echo(f"seed:          {inst.seed}")
    typer.echo(f"fleet_size:    {inst.fleet_size}")
    typer.echo(f"vehicle_capacity: {inst.vehicle_capacity}")
    typer.echo(f"created_at:    {inst.created_at}")
    if inst.depot is None:
        typer.echo("depot:         (none set)")
    else:
        d = inst.depot
        typer.echo(
            f"depot:         {d.id} {d.label!r} ({d.lat:.5f}, {d.lon:.5f}) "
            f"node={d.node} source={d.source.value}"
        )
    typer.echo(f"stops (N={inst.n_stops}):")
    for s in inst.stops:
        typer.echo(
            f"  {s.id}: {s.label!r} ({s.lat:.5f}, {s.lon:.5f}) "
            f"node={s.node} source={s.source.value}"
        )

    try:
        builder.build()
        typer.echo("status:        ready (passes validation)")
    except Exception as exc:  # noqa: BLE001 - report any validation problem, not a crash
        typer.echo(f"status:        NOT ready — {exc}")


@instance_app.command("map")
def instance_map(
    name: str = typer.Option(..., "--name"),
    out: str | None = typer.Option(None, "--out", help="Output HTML path."),
) -> None:
    """Render a standalone Folium HTML map of an instance's depot and stops."""
    from dlm.config import settings
    from dlm.viz.folium_map import save_instance_map

    builder = _load_builder(name)
    out_path = Path(out) if out else settings.results_dir / "instance_maps" / f"{name}.html"
    saved = save_instance_map(builder.instance, out_path)
    typer.echo(f"Map written to {saved}")


@instance_app.command("matrix")
def instance_matrix(
    name: str = typer.Option(..., "--name"),
    force: bool = typer.Option(False, "--force", help="Ignore the cache and rebuild."),
) -> None:
    """Build (or load from cache) the travel-time matrix over an instance's
    depot + stops, and report its stats."""
    from dlm.instance.builder import InstanceBuilder
    from dlm.instance.matrix import build_matrix
    from dlm.network.loader import build_graph

    path = _instance_path(name)
    if not path.exists():
        typer.echo(
            f"No instance named {name!r} (looked in {path}). Create one with `dlm instance new`."
        )
        raise typer.Exit(code=1)

    graph, report = build_graph()
    builder = InstanceBuilder.load(graph, path)
    try:
        inst = builder.build()
    except Exception as exc:  # noqa: BLE001 - report validation problems, not a crash
        typer.echo(f"Instance {name!r} is not ready: {exc}")
        raise typer.Exit(code=1) from exc

    nodes = [inst.depot.node, *(s.node for s in inst.stops)]
    _, stats = build_matrix(graph, nodes, graph_id=report.cache_path.stem, force_rebuild=force)

    typer.echo(f"cache:              {'hit' if stats.from_cache else 'built fresh'}")
    typer.echo(f"build time:         {stats.build_seconds:.3f}s")
    typer.echo(f"points:             {stats.n_points}")
    typer.echo(f"ordered pairs:      {stats.n_ordered_pairs}")
    typer.echo(f"asymmetric pairs:   {stats.asymmetric_pairs} ({100 * stats.asymmetry_rate:.1f}%)")
    typer.echo(f"triangle violations: {stats.triangle_violations}")


def _plan_fleet(instance: str, inst, graph, graph_report, run_id: str, run_dir) -> None:  # noqa: ANN001 - avoid heavy imports at module load
    import json

    import yaml

    from dlm.config import settings
    from dlm.instance.matrix import build_matrix
    from dlm.simulation.metrics import compute_fleet_t1
    from dlm.solver.clarke_wright import ClarkeWrightSolver
    from dlm.viz.folium_map import save_fleet_route_map

    nodes = [inst.depot.node, *(s.node for s in inst.stops)]
    matrix, _ = build_matrix(graph, nodes, graph_id=graph_report.cache_path.stem)
    fleet = ClarkeWrightSolver().solve_fleet(inst, matrix)
    t1 = compute_fleet_t1(inst, fleet)

    config = {
        "instance": instance,
        "solver": "clarke_wright_2opt",
        "fleet_size": inst.fleet_size,
        "vehicle_capacity": inst.vehicle_capacity,
        "seed": inst.seed,
        "default_service_time_s": settings.default_service_time_s,
        "graph_cache_key": graph_report.cache_path.stem,
    }
    (run_dir / "config.yaml").write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")

    result = {
        "run_id": run_id,
        "instance": instance,
        "n_vehicles_used": len(fleet.routes),
        "unassigned": fleet.unassigned,
        "T1": {
            "drive_time_s": t1.drive_time_s,
            "service_time_s": t1.service_time_s,
            "total_time_s": t1.total_time_s,
            "distance_m": t1.distance_m,
            "n_stops_served": t1.n_stops_served,
            "n_stops_unassigned": t1.n_stops_unassigned,
        },
        "routes": [
            {"vehicle": i + 1, "order": s.order, "drive_time_s": s.total_time_s}
            for i, s in enumerate(fleet.routes)
        ],
        "solver_meta": {k: v for k, v in fleet.meta.items()},
    }
    (run_dir / "result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")

    save_fleet_route_map(inst, fleet, graph, run_dir / "route_map.html")

    typer.echo("solver:            clarke_wright_2opt")
    typer.echo(f"vehicles used:     {len(fleet.routes)} / {inst.fleet_size}")
    for i, s in enumerate(fleet.routes, start=1):
        typer.echo(f"  vehicle {i}: {' -> '.join(s.order)}  ({s.total_time_s:.1f}s)")
    if fleet.unassigned:
        typer.echo(f"UNASSIGNED:        {', '.join(fleet.unassigned)}")
    typer.echo(f"drive time:        {t1.drive_time_s:.1f}s")
    typer.echo(f"service time:      {t1.service_time_s:.1f}s ({t1.n_stops_served} stops)")
    typer.echo(f"T1 (total time):   {t1.total_time_s:.1f}s")
    typer.echo(f"distance:          {t1.distance_m:.1f}m")
    typer.echo(f"written to:        {run_dir}")


@app.command("plan")
def plan(
    instance: str = typer.Option(..., "--instance", help="Instance name."),
    solver: str = typer.Option(
        "nn_2opt",
        "--solver",
        help="'nn_2opt' or 'nearest_neighbour' (fleet_size == 1 only — "
        "fleet_size > 1 always uses Clarke-Wright + 2-opt).",
    ),
) -> None:
    """Solve an instance's baseline route and report T1.

    `fleet_size == 1` (the default): single route, `--solver` selects
    `nn_2opt`/`nearest_neighbour`. `fleet_size > 1`: multiple vehicle
    routes via Clarke-Wright + 2-opt (Stage 8) — `--solver` is ignored.

    Writes `results/<instance>-<timestamp>/` with `config.yaml`,
    `result.json`, and `route_map.html`.
    """
    import json
    from datetime import UTC, datetime

    import yaml

    from dlm.config import settings
    from dlm.instance.builder import InstanceBuilder
    from dlm.instance.matrix import build_matrix
    from dlm.network.loader import build_graph
    from dlm.simulation.metrics import compute_t1
    from dlm.solver.nearest_neighbour import NearestNeighbourSolver
    from dlm.solver.two_opt import TwoOptSolver
    from dlm.viz.folium_map import save_route_map

    path = _instance_path(instance)
    if not path.exists():
        typer.echo(f"No instance named {instance!r} (looked in {path}).")
        raise typer.Exit(code=1)

    graph, graph_report = build_graph()
    builder = InstanceBuilder.load(graph, path)
    try:
        inst = builder.build()
    except Exception as exc:  # noqa: BLE001 - report validation problems, not a crash
        typer.echo(f"Instance {instance!r} is not ready: {exc}")
        raise typer.Exit(code=1) from exc

    run_id = f"{instance}-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}"
    run_dir = settings.results_dir / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    if inst.fleet_size > 1:
        _plan_fleet(instance, inst, graph, graph_report, run_id, run_dir)
        return

    nodes = [inst.depot.node, *(s.node for s in inst.stops)]
    matrix, _ = build_matrix(graph, nodes, graph_id=graph_report.cache_path.stem)

    solvers = {"nn_2opt": TwoOptSolver(), "nearest_neighbour": NearestNeighbourSolver()}
    if solver not in solvers:
        typer.echo(f"Unknown solver {solver!r}. Choices: {', '.join(solvers)}")
        raise typer.Exit(code=1)
    solution = solvers[solver].solve(inst, matrix)
    t1 = compute_t1(inst, solution)

    config = {
        "instance": instance,
        "solver": solver,
        "seed": inst.seed,
        "default_service_time_s": settings.default_service_time_s,
        "graph_cache_key": graph_report.cache_path.stem,
    }
    (run_dir / "config.yaml").write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")

    result = {
        "run_id": run_id,
        "instance": instance,
        "order": solution.order,
        "T1": {
            "drive_time_s": t1.drive_time_s,
            "service_time_s": t1.service_time_s,
            "total_time_s": t1.total_time_s,
            "distance_m": t1.distance_m,
            "n_stops_served": t1.n_stops_served,
        },
        "legs": [
            {
                "from": leg.from_id,
                "to": leg.to_id,
                "travel_time_s": leg.travel_time_s,
                "distance_m": leg.distance_m,
            }
            for leg in t1.legs
        ],
        "solver_meta": {k: v for k, v in solution.meta.items() if k != "two_opt_trajectory"},
    }
    (run_dir / "result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")

    save_route_map(inst, solution, graph, run_dir / "route_map.html")

    typer.echo(f"solver:            {solver}")
    typer.echo(f"order:             {' -> '.join(solution.order)}")
    typer.echo(f"drive time:        {t1.drive_time_s:.1f}s")
    typer.echo(f"service time:      {t1.service_time_s:.1f}s ({t1.n_stops_served} stops)")
    typer.echo(f"T1 (total time):   {t1.total_time_s:.1f}s")
    typer.echo(f"distance:          {t1.distance_m:.1f}m")
    typer.echo(f"written to:        {run_dir}")


def _t2_line(label: str, t2) -> str:  # noqa: ANN001 - T2Result, avoid import at module load
    if not t2.feasible:
        return f"{label}: INFEASIBLE (a required leg has no path on the disrupted graph)"
    return f"{label}: {t2.total_time_s:.1f}s (drive {t2.drive_time_s:.1f}s)"


@app.command("compare")
def compare(
    instance: str = typer.Option(..., "--instance", help="Instance name."),
    scenario: str = typer.Option(..., "--scenario", help="Scenario name (see `dlm disrupt list`)."),
    solver: str = typer.Option("nn_2opt", "--solver", help="'nn_2opt' or 'nearest_neighbour'."),
) -> None:
    """Compute T1/T2/T3/Saving % for an instance under a disruption
    scenario.

    Writes `results/<instance>-vs-<scenario>-<timestamp>/` with
    `config.yaml`, `result.json`, `route_map.html` (the T1 plan), and
    `disruption_map.html` (what the scenario changed).
    """
    import json
    from datetime import UTC, datetime

    import yaml

    from dlm.config import settings
    from dlm.disruption.engine import apply_scenario
    from dlm.disruption.schema import ScenarioNotFoundError, find_scenario, load_scenario
    from dlm.instance.builder import InstanceBuilder
    from dlm.instance.matrix import build_matrix
    from dlm.network.loader import build_graph
    from dlm.simulation.execution import InformationModel
    from dlm.simulation.metrics import (
        compute_saving,
        compute_t1,
        compute_t2,
        compute_t3,
        compute_t3_oracle,
    )
    from dlm.solver.nearest_neighbour import NearestNeighbourSolver
    from dlm.solver.two_opt import TwoOptSolver
    from dlm.viz.folium_map import save_disruption_map, save_route_map

    inst_path = _instance_path(instance)
    if not inst_path.exists():
        typer.echo(f"No instance named {instance!r} (looked in {inst_path}).")
        raise typer.Exit(code=1)
    try:
        scenario_path = find_scenario(scenario)
    except ScenarioNotFoundError as exc:
        typer.echo(f"Error: {exc}")
        raise typer.Exit(code=1) from exc
    sc = load_scenario(scenario_path)

    graph, graph_report = build_graph()
    builder = InstanceBuilder.load(graph, inst_path)
    try:
        inst = builder.build()
    except Exception as exc:  # noqa: BLE001 - report validation problems, not a crash
        typer.echo(f"Instance {instance!r} is not ready: {exc}")
        raise typer.Exit(code=1) from exc

    nodes = [inst.depot.node, *(s.node for s in inst.stops)]
    matrix, _ = build_matrix(graph, nodes, graph_id=graph_report.cache_path.stem)
    solvers = {"nn_2opt": TwoOptSolver(), "nearest_neighbour": NearestNeighbourSolver()}
    if solver not in solvers:
        typer.echo(f"Unknown solver {solver!r}. Choices: {', '.join(solvers)}")
        raise typer.Exit(code=1)
    solution = solvers[solver].solve(inst, matrix)
    t1 = compute_t1(inst, solution)

    disruption_result = apply_scenario(graph, sc)
    t2_omniscient = compute_t2(inst, solution, disruption_result.graph, InformationModel.OMNISCIENT)
    t2_reactive = compute_t2(inst, solution, disruption_result.graph, InformationModel.REACTIVE)
    t3 = compute_t3(inst, solution, disruption_result.graph)
    t3_oracle = compute_t3_oracle(inst, disruption_result.graph)
    saving_pct = compute_saving(t2_reactive, t3)

    run_id = f"{instance}-vs-{scenario}-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}"
    run_dir = settings.results_dir / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    config = {
        "instance": instance,
        "scenario": scenario,
        "solver": solver,
        "seed": inst.seed,
        "default_service_time_s": settings.default_service_time_s,
        "graph_cache_key": graph_report.cache_path.stem,
    }
    (run_dir / "config.yaml").write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")

    def _t2_json(t2):  # noqa: ANN001, ANN202
        return {
            "information_model": t2.information_model.value,
            "feasible": t2.feasible,
            "drive_time_s": t2.drive_time_s,
            "total_time_s": t2.total_time_s,
            "distance_m": t2.distance_m,
        }

    result = {
        "run_id": run_id,
        "instance": instance,
        "scenario": scenario,
        "T1": {"total_time_s": t1.total_time_s, "drive_time_s": t1.drive_time_s},
        "T2_omniscient": _t2_json(t2_omniscient),
        "T2_reactive": _t2_json(t2_reactive),
        "T3": {
            "triggered": t3.triggered,
            "feasible": t3.feasible,
            "drive_time_s": t3.drive_time_s,
            "total_time_s": t3.total_time_s,
            "distance_m": t3.distance_m,
            "order": t3.order,
        },
        "T3_oracle": {
            "feasible": t3_oracle.feasible,
            "drive_time_s": t3_oracle.drive_time_s,
            "total_time_s": t3_oracle.total_time_s,
            "distance_m": t3_oracle.distance_m,
            "order": t3_oracle.order,
        },
        "saving_pct": saving_pct,
        "disruption": {
            "n_edges_closed": disruption_result.n_edges_closed,
            "n_edges_slowed": disruption_result.n_edges_slowed,
        },
    }
    (run_dir / "result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")

    save_route_map(inst, solution, graph, run_dir / "route_map.html")
    save_disruption_map(disruption_result, run_dir / "disruption_map.html")

    typer.echo(f"instance:          {instance}  scenario: {scenario}")
    typer.echo(
        f"edges closed/slowed: {disruption_result.n_edges_closed}/"
        f"{disruption_result.n_edges_slowed}"
    )
    typer.echo(f"T1 (normal):       {t1.total_time_s:.1f}s")
    typer.echo(_t2_line("T2 (omniscient)", t2_omniscient))
    typer.echo(_t2_line("T2 (reactive)  ", t2_reactive))
    if not t3.feasible:
        typer.echo("T3 (re-optimised): INFEASIBLE")
    else:
        typer.echo(
            f"T3 (re-optimised): {t3.total_time_s:.1f}s (drive {t3.drive_time_s:.1f}s, "
            f"replan {'triggered' if t3.triggered else 'not needed'})"
        )
    if not t3_oracle.feasible:
        typer.echo("T3_oracle (best possible): INFEASIBLE")
    else:
        typer.echo(f"T3_oracle (best possible): {t3_oracle.total_time_s:.1f}s")
    if saving_pct is not None:
        typer.echo(f"Saving %:          {saving_pct:.1f}%  (vs T2 reactive)")
    elif t2_reactive.feasible and not t3.feasible:
        typer.echo("Saving %:          n/a (T3 also infeasible)")
    elif not t2_reactive.feasible and t3.feasible:
        typer.echo(
            "Saving %:          n/a (T2 infeasible, but re-optimising recovers a feasible route)"
        )
    typer.echo(f"written to:        {run_dir}")


def _scenario_template(name: str, shape: object, effect: object) -> str:  # noqa: ANN001
    from dlm.disruption.schema import DisruptionEffect, DisruptionShape

    geometry_lines = {
        DisruptionShape.EDGE: (
            "    from_latlon: [53.3498, -6.2603]   # TODO: replace with real points\n"
            "    to_latlon: [53.3478, -6.2597]\n"
        ),
        DisruptionShape.NODE: "    at: [53.3498, -6.2603]   # TODO: replace with a real point\n",
        DisruptionShape.CORRIDOR: (
            "    waypoints:                        # TODO: replace with real waypoints, in order\n"
            "      - [53.3498, -6.2603]\n"
            "      - [53.3478, -6.2597]\n"
        ),
        DisruptionShape.POLYGON: (
            "    boundary:                         # TODO: replace with a real boundary ring\n"
            "      - [53.3490, -6.2610]\n"
            "      - [53.3490, -6.2595]\n"
            "      - [53.3505, -6.2595]\n"
            "      - [53.3505, -6.2610]\n"
        ),
    }
    effect_line = (
        "    speed_factor: 0.5   # fraction of normal speed, 0 < x < 1\n"
        if (effect is DisruptionEffect.SLOW_ZONE)
        else ""
    )
    return (
        f"schema_version: 1\n"
        f"name: {name}\n"
        f"description: TODO\n"
        f"disruptions:\n"
        f"  - id: {name}_1\n"
        f"    shape: {shape.value}\n"
        f"    effect: {effect.value}\n"
        f"{geometry_lines[shape]}"
        f"{effect_line}"
        f"    severity: 1.0\n"
    )


@disrupt_app.command("list")
def disrupt_list() -> None:
    """List every scenario YAML file found under `scenarios/` (recursive)."""
    from dlm.config import settings
    from dlm.disruption.schema import list_scenarios, load_scenario

    paths = list_scenarios()
    if not paths:
        typer.echo(f"No scenarios found in {settings.scenarios_dir}.")
        return
    for path in paths:
        rel = path.relative_to(settings.scenarios_dir)
        try:
            sc = load_scenario(path)
            typer.echo(f"{path.stem} ({rel}): {len(sc.disruptions)} disruption(s)")
        except Exception as exc:  # noqa: BLE001 - report bad YAML, not a crash
            typer.echo(f"{path.stem} ({rel}): INVALID — {exc}")


@disrupt_app.command("validate")
def disrupt_validate(scenario: str = typer.Option(..., "--scenario")) -> None:
    """Resolve every disruption in a scenario against the real Dublin
    graph and report problems, without applying anything."""
    from dlm.disruption.engine import validate_scenario
    from dlm.disruption.schema import ScenarioNotFoundError, find_scenario, load_scenario
    from dlm.network.loader import build_graph

    try:
        path = find_scenario(scenario)
    except ScenarioNotFoundError as exc:
        typer.echo(f"Error: {exc}")
        raise typer.Exit(code=1) from exc

    try:
        sc = load_scenario(path)
    except Exception as exc:  # noqa: BLE001 - report bad YAML, not a crash
        typer.echo(f"Error: {path} is not a valid scenario: {exc}")
        raise typer.Exit(code=1) from exc

    graph, _ = build_graph()
    report = validate_scenario(graph, sc)

    typer.echo(f"scenario:          {sc.name} ({path})")
    typer.echo(f"disruptions:       {report.n_disruptions}")
    for disruption_id, count in report.resolved_edge_counts.items():
        typer.echo(f"  {disruption_id}: resolves to {count} edge(s)")
    if report.valid:
        typer.echo("status:            VALID")
        return
    typer.echo("status:            INVALID")
    for err in report.errors:
        typer.echo(f"  - {err}")
    raise typer.Exit(code=1)


@disrupt_app.command("preview")
def disrupt_preview(
    scenario: str = typer.Option(..., "--scenario"),
    at_time: float | None = typer.Option(
        None,
        "--at-time",
        help="Seconds since scenario start; omit to apply every disruption regardless "
        "of its time window.",
    ),
    out: str | None = typer.Option(None, "--out", help="Output HTML path."),
) -> None:
    """Apply a scenario to the real Dublin graph, report the audit (edges
    closed/slowed, whether the graph is still strongly connected), and
    render a map of the affected edges."""
    import networkx as nx

    from dlm.config import settings
    from dlm.disruption.engine import DisruptionResolutionError, apply_scenario
    from dlm.disruption.schema import ScenarioNotFoundError, find_scenario, load_scenario
    from dlm.network.loader import build_graph
    from dlm.viz.folium_map import save_disruption_map

    try:
        path = find_scenario(scenario)
    except ScenarioNotFoundError as exc:
        typer.echo(f"Error: {exc}")
        raise typer.Exit(code=1) from exc
    sc = load_scenario(path)

    graph, _ = build_graph()
    try:
        result = apply_scenario(graph, sc, at_time=at_time)
    except DisruptionResolutionError as exc:
        typer.echo(f"Error applying {scenario!r}: {exc}")
        raise typer.Exit(code=1) from exc

    still_connected = nx.is_strongly_connected(result.graph)

    typer.echo(f"scenario:          {sc.name} ({path})")
    typer.echo(f"edges closed:      {result.n_edges_closed}")
    typer.echo(f"edges slowed:      {result.n_edges_slowed}")
    typer.echo(f"strongly connected after: {still_connected}")

    out_path = (
        Path(out) if out else settings.results_dir / "disruption_previews" / f"{scenario}.html"
    )
    saved = save_disruption_map(result, out_path)
    typer.echo(f"map written to:    {saved}")


@disrupt_app.command("new")
def disrupt_new(
    name: str = typer.Option(..., "--name", help="Scenario name (used as the save filename)."),
    shape: str = typer.Option(..., "--shape", help="edge | node | corridor | polygon"),
    effect: str = typer.Option(..., "--effect", help="closure | slow_zone"),
    force: bool = typer.Option(
        False, "--force", help="Overwrite an existing scenario of this name."
    ),
) -> None:
    """Scaffold a new scenario YAML at `scenarios/<name>.yaml` with a
    template disruption for the given shape/effect, ready to hand-edit."""
    from dlm.config import settings
    from dlm.disruption.schema import DisruptionEffect, DisruptionShape

    try:
        shape_enum = DisruptionShape(shape)
    except ValueError as exc:
        typer.echo(
            f"Unknown shape {shape!r}. Choices: {', '.join(s.value for s in DisruptionShape)}"
        )
        raise typer.Exit(code=1) from exc
    try:
        effect_enum = DisruptionEffect(effect)
    except ValueError as exc:
        typer.echo(
            f"Unknown effect {effect!r}. Choices: {', '.join(e.value for e in DisruptionEffect)}"
        )
        raise typer.Exit(code=1) from exc

    path = settings.scenarios_dir / f"{name}.yaml"
    if path.exists() and not force:
        typer.echo(f"Scenario {name!r} already exists at {path}. Use --force to overwrite.")
        raise typer.Exit(code=1)

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_scenario_template(name, shape_enum, effect_enum), encoding="utf-8")
    typer.echo(f"Scaffolded {path}")
    typer.echo(f"Edit the placeholder geometry, then: dlm disrupt validate --scenario {name}")


@app.command("batch")
def batch(
    instances: str = typer.Option(
        "small,medium,large", "--instances", help="Comma-separated instance names."
    ),
    n_random: int = typer.Option(
        10, "--n-random", help="Seeded random scenarios to add on top of the curated library."
    ),
    seed: int = typer.Option(42, "--seed", help="Base seed for random scenario generation."),
    out: str | None = typer.Option(
        None,
        "--out",
        help="Also write the results CSV here (default docs/report/batch_results.csv).",
    ),
    solver: str = typer.Option("nn_2opt", "--solver", help="'nn_2opt' or 'nearest_neighbour'."),
) -> None:
    """Run T1/T2/T3/Saving % across every (instance, scenario) pair — the
    curated scenario library plus `--n-random` seeded random scenarios —
    and write an aggregate results table.

    Writes `results/batch-<timestamp>/{config.yaml, batch_results.csv,
    summary.json}`, and a copy of the CSV to `--out` (the committed
    dataset `dlm figures` and the report are built from).
    """
    import json
    from datetime import UTC, datetime

    import pandas as pd
    import yaml

    from dlm.config import REPO_ROOT, settings
    from dlm.disruption.engine import DisruptionResolutionError, apply_scenario
    from dlm.disruption.generators import generate_random_scenarios
    from dlm.disruption.schema import list_scenarios, load_scenario
    from dlm.instance.builder import InstanceBuilder
    from dlm.instance.matrix import build_matrix
    from dlm.network.loader import build_graph
    from dlm.simulation.execution import InformationModel
    from dlm.simulation.metrics import (
        compute_saving,
        compute_t1,
        compute_t2,
        compute_t3,
        compute_t3_oracle,
    )
    from dlm.solver.nearest_neighbour import NearestNeighbourSolver
    from dlm.solver.two_opt import TwoOptSolver

    instance_names = [s.strip() for s in instances.split(",") if s.strip()]
    solvers = {"nn_2opt": TwoOptSolver(), "nearest_neighbour": NearestNeighbourSolver()}
    if solver not in solvers:
        typer.echo(f"Unknown solver {solver!r}. Choices: {', '.join(solvers)}")
        raise typer.Exit(code=1)

    graph, graph_report = build_graph()

    curated_paths = [p for p in list_scenarios() if p.parent.name == "library"]
    scenarios = [(load_scenario(p), "curated") for p in curated_paths]
    if n_random > 0:
        scenarios += [
            (sc, "random") for sc in generate_random_scenarios(graph, n_random, base_seed=seed)
        ]

    rows: list[dict] = []
    for inst_name in instance_names:
        path = _instance_path(inst_name)
        if not path.exists():
            typer.echo(f"Skipping unknown instance {inst_name!r} (looked in {path}).")
            continue
        inst = InstanceBuilder.load(graph, path).build()
        nodes = [inst.depot.node, *(s.node for s in inst.stops)]
        matrix, _ = build_matrix(graph, nodes, graph_id=graph_report.cache_path.stem)
        solution = solvers[solver].solve(inst, matrix)
        t1 = compute_t1(inst, solution)

        for sc, kind in scenarios:
            try:
                result = apply_scenario(graph, sc)
            except DisruptionResolutionError as exc:
                typer.echo(f"  {inst_name} / {sc.name}: skipped, resolution failed: {exc}")
                continue
            d0 = sc.disruptions[0]
            t2_omni = compute_t2(inst, solution, result.graph, InformationModel.OMNISCIENT)
            t2_reactive = compute_t2(inst, solution, result.graph, InformationModel.REACTIVE)
            t3 = compute_t3(inst, solution, result.graph)
            t3_oracle = compute_t3_oracle(inst, result.graph)
            rows.append(
                {
                    "instance": inst_name,
                    "n_stops": inst.n_stops,
                    "scenario": sc.name,
                    "scenario_kind": kind,
                    "shape": d0.shape.value,
                    "effect": d0.effect.value,
                    "edges_closed": result.n_edges_closed,
                    "edges_slowed": result.n_edges_slowed,
                    "T1_total_s": t1.total_time_s,
                    "T2_omniscient_feasible": t2_omni.feasible,
                    "T2_omniscient_total_s": t2_omni.total_time_s,
                    "T2_reactive_feasible": t2_reactive.feasible,
                    "T2_reactive_total_s": t2_reactive.total_time_s,
                    "T3_feasible": t3.feasible,
                    "T3_triggered": t3.triggered,
                    "T3_total_s": t3.total_time_s,
                    "T3_oracle_feasible": t3_oracle.feasible,
                    "T3_oracle_total_s": t3_oracle.total_time_s,
                    "saving_pct": compute_saving(t2_reactive, t3),
                }
            )

    df = pd.DataFrame(rows)
    run_id = f"batch-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}"
    run_dir = settings.results_dir / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    df.to_csv(run_dir / "batch_results.csv", index=False)

    config = {
        "instances": instance_names,
        "n_random": n_random,
        "seed": seed,
        "solver": solver,
        "n_curated_scenarios": len(curated_paths),
        "graph_cache_key": graph_report.cache_path.stem,
    }
    (run_dir / "config.yaml").write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")

    saving_values = df["saving_pct"].dropna()
    summary = {
        "n_runs": len(df),
        "n_feasible_t2_reactive": int(df["T2_reactive_feasible"].sum()),
        "n_feasible_t3": int(df["T3_feasible"].sum()),
        "mean_saving_pct": float(saving_values.mean()) if len(saving_values) else None,
    }
    (run_dir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")

    out_path = Path(out) if out else REPO_ROOT / "docs" / "report" / "batch_results.csv"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out_path, index=False)

    typer.echo(f"instances:         {', '.join(instance_names)}")
    typer.echo(f"scenarios:         {len(curated_paths)} curated + {n_random} random")
    typer.echo(f"runs:              {len(df)}")
    typer.echo(f"T2(reactive) feasible: {summary['n_feasible_t2_reactive']}/{len(df)}")
    typer.echo(f"T3 feasible:       {summary['n_feasible_t3']}/{len(df)}")
    if summary["mean_saving_pct"] is not None:
        typer.echo(f"mean Saving %:     {summary['mean_saving_pct']:.1f}%")
    typer.echo(f"written to:        {run_dir}")
    typer.echo(f"also written to:   {out_path}")


@app.command("figures")
def figures(
    results_csv: str | None = typer.Option(
        None, "--results", help="Batch results CSV (default docs/report/batch_results.csv)."
    ),
    instance: str | None = typer.Option(
        None,
        "--instance",
        help="Instance for the per-scenario comparison figure (default: first in file).",
    ),
    out: str | None = typer.Option(
        None, "--out", help="Output directory (default docs/report/figures)."
    ),
) -> None:
    """Regenerate the report's figures from a `dlm batch` results CSV.

    Pure post-processing: reads a CSV, writes PNG+SVG figures — no graph,
    instance, or solver access needed, so this is fast and safe to re-run
    any time the CSV or the figure styling changes.
    """
    from dlm.config import REPO_ROOT
    from dlm.viz.figures import make_all_figures

    csv_path = (
        Path(results_csv) if results_csv else REPO_ROOT / "docs" / "report" / "batch_results.csv"
    )
    if not csv_path.exists():
        typer.echo(f"No results CSV at {csv_path}. Run `dlm batch` first.")
        raise typer.Exit(code=1)
    out_dir = Path(out) if out else REPO_ROOT / "docs" / "report" / "figures"

    written = make_all_figures(csv_path, out_dir, instance=instance)
    for png_path, svg_path in written:
        typer.echo(f"wrote {png_path.name} / {svg_path.name}")
    typer.echo(f"written to:        {out_dir}")


@app.command("sensitivity")
def sensitivity(
    instances: str = typer.Option(
        "small,medium,large", "--instances", help="Comma-separated instance names."
    ),
    values: str = typer.Option(
        "60,120,180,240,300",
        "--values",
        help="Comma-separated default_service_time_s values to sweep, in seconds.",
    ),
    solver: str = typer.Option("nn_2opt", "--solver", help="'nn_2opt' or 'nearest_neighbour'."),
    out: str | None = typer.Option(
        None,
        "--out",
        help="Also write the results CSV here (default docs/report/sensitivity_results.csv).",
    ),
) -> None:
    """Sweep `default_service_time_s` and report how sensitive `T1` is —
    the concrete check ADR-0004 (Stage 4) asked for before its 180s
    default is treated as final.

    Each instance is solved once (the solver never sees service time —
    Stage 4's `compute_t1` docstring — so the route and `drive_time_s`
    are identical at every value); only `service_time_s`/`total_time_s`
    and service time's share of the total change.
    """
    import json
    from datetime import UTC, datetime

    import pandas as pd
    import yaml

    from dlm.config import REPO_ROOT, settings
    from dlm.instance.builder import InstanceBuilder
    from dlm.instance.matrix import build_matrix
    from dlm.network.loader import build_graph
    from dlm.simulation.metrics import compute_t1
    from dlm.solver.nearest_neighbour import NearestNeighbourSolver
    from dlm.solver.two_opt import TwoOptSolver

    instance_names = [s.strip() for s in instances.split(",") if s.strip()]
    try:
        service_time_values = [float(v.strip()) for v in values.split(",") if v.strip()]
    except ValueError as exc:
        typer.echo(f"--values must be comma-separated numbers, got {values!r}")
        raise typer.Exit(code=1) from exc
    solvers = {"nn_2opt": TwoOptSolver(), "nearest_neighbour": NearestNeighbourSolver()}
    if solver not in solvers:
        typer.echo(f"Unknown solver {solver!r}. Choices: {', '.join(solvers)}")
        raise typer.Exit(code=1)

    graph, graph_report = build_graph()

    rows: list[dict] = []
    for inst_name in instance_names:
        path = _instance_path(inst_name)
        if not path.exists():
            typer.echo(f"Skipping unknown instance {inst_name!r} (looked in {path}).")
            continue
        inst = InstanceBuilder.load(graph, path).build()
        nodes = [inst.depot.node, *(s.node for s in inst.stops)]
        matrix, _ = build_matrix(graph, nodes, graph_id=graph_report.cache_path.stem)
        solution = solvers[solver].solve(inst, matrix)

        for value in service_time_values:
            t1 = compute_t1(inst, solution, default_service_time_s=value)
            rows.append(
                {
                    "instance": inst_name,
                    "n_stops": inst.n_stops,
                    "default_service_time_s": value,
                    "drive_time_s": t1.drive_time_s,
                    "service_time_s": t1.service_time_s,
                    "total_time_s": t1.total_time_s,
                    "service_time_share_pct": 100.0 * t1.service_time_s / t1.total_time_s,
                }
            )

    df = pd.DataFrame(rows)
    run_id = f"sensitivity-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}"
    run_dir = settings.results_dir / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    df.to_csv(run_dir / "sensitivity_results.csv", index=False)
    config = {
        "instances": instance_names,
        "values": service_time_values,
        "solver": solver,
        "graph_cache_key": graph_report.cache_path.stem,
    }
    (run_dir / "config.yaml").write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    (run_dir / "summary.json").write_text(
        json.dumps(
            {
                "min_service_time_share_pct": float(df["service_time_share_pct"].min()),
                "max_service_time_share_pct": float(df["service_time_share_pct"].max()),
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    out_path = Path(out) if out else REPO_ROOT / "docs" / "report" / "sensitivity_results.csv"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out_path, index=False)

    for inst_name in instance_names:
        sub = df[df["instance"] == inst_name]
        if sub.empty:
            continue
        typer.echo(f"{inst_name} (drive time fixed at {sub['drive_time_s'].iloc[0]:.1f}s):")
        for _, row in sub.iterrows():
            typer.echo(
                f"  service_time={row['default_service_time_s']:.0f}s -> "
                f"T1={row['total_time_s']:.1f}s "
                f"(service time = {row['service_time_share_pct']:.1f}% of T1)"
            )
    typer.echo(f"written to:        {run_dir}")
    typer.echo(f"also written to:   {out_path}")


@app.command("benchmark")
def benchmark(
    instances: str = typer.Option(
        "small,medium,large,fleet", "--instances", help="Comma-separated instance names."
    ),
    time_limit_s: float = typer.Option(10.0, "--time-limit", help="OR-Tools search time budget."),
    out: str | None = typer.Option(
        None,
        "--out",
        help="Also write the results CSV here (default docs/report/benchmark_results.csv).",
    ),
) -> None:
    """Compare the hand-implemented solver (`nn_2opt` for `fleet_size ==
    1`, `clarke_wright_2opt` for `fleet_size > 1`) against the OR-Tools
    benchmark oracle: solution quality (`gap_pct`, positive = hand-
    implemented costs more) and runtime, per instance.

    Writes `results/benchmark-<timestamp>/` and a committed CSV
    (`--out`, default `docs/report/benchmark_results.csv`).
    """
    import time
    from datetime import UTC, datetime

    import pandas as pd
    import yaml

    from dlm.config import REPO_ROOT, settings
    from dlm.instance.builder import InstanceBuilder
    from dlm.instance.matrix import build_matrix
    from dlm.network.loader import build_graph
    from dlm.solver.clarke_wright import ClarkeWrightSolver
    from dlm.solver.ortools_solver import OrToolsSolutionNotFound, OrToolsSolver
    from dlm.solver.two_opt import TwoOptSolver

    instance_names = [s.strip() for s in instances.split(",") if s.strip()]
    graph, graph_report = build_graph()

    rows: list[dict] = []
    for inst_name in instance_names:
        path = _instance_path(inst_name)
        if not path.exists():
            typer.echo(f"Skipping unknown instance {inst_name!r} (looked in {path}).")
            continue
        inst = InstanceBuilder.load(graph, path).build()
        nodes = [inst.depot.node, *(s.node for s in inst.stops)]
        matrix, _ = build_matrix(graph, nodes, graph_id=graph_report.cache_path.stem)

        if inst.fleet_size == 1:
            t0 = time.time()
            hand_solution = TwoOptSolver().solve(inst, matrix)
            hand_runtime_s = time.time() - t0
            hand_solver_name = "nn_2opt"
            hand_total_s = hand_solution.total_time_s
            hand_n_served = len(hand_solution.order)
        else:
            t0 = time.time()
            hand_fleet = ClarkeWrightSolver().solve_fleet(inst, matrix)
            hand_runtime_s = time.time() - t0
            hand_solver_name = "clarke_wright_2opt"
            hand_total_s = hand_fleet.total_time_s
            hand_n_served = sum(len(r.order) for r in hand_fleet.routes)

        t0 = time.time()
        try:
            or_fleet = OrToolsSolver(time_limit_s=time_limit_s).solve_fleet(
                inst, matrix, apply_time_windows=False
            )
            or_runtime_s = time.time() - t0
            or_total_s: float | None = or_fleet.total_time_s
            or_n_served = sum(len(r.order) for r in or_fleet.routes)
            or_status = "ok"
        except OrToolsSolutionNotFound:
            or_runtime_s = time.time() - t0
            or_total_s = None
            or_n_served = 0
            or_status = "no_solution"

        gap_pct = (
            100.0 * (hand_total_s - or_total_s) / or_total_s
            if or_total_s not in (None, 0)
            else None
        )
        rows.append(
            {
                "instance": inst_name,
                "n_stops": inst.n_stops,
                "fleet_size": inst.fleet_size,
                "hand_solver": hand_solver_name,
                "hand_total_time_s": hand_total_s,
                "hand_n_served": hand_n_served,
                "hand_runtime_s": hand_runtime_s,
                "ortools_total_time_s": or_total_s,
                "ortools_n_served": or_n_served,
                "ortools_runtime_s": or_runtime_s,
                "ortools_status": or_status,
                "gap_pct": gap_pct,
            }
        )
        or_str = "n/a" if or_total_s is None else f"{or_total_s:.1f}s"
        gap_str = "n/a" if gap_pct is None else f"{gap_pct:+.1f}%"
        typer.echo(
            f"{inst_name}: hand={hand_total_s:.1f}s ({hand_runtime_s * 1000:.1f}ms)  "
            f"or-tools={or_str} ({or_runtime_s:.2f}s)  gap={gap_str}"
        )

    df = pd.DataFrame(rows)
    run_id = f"benchmark-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}"
    run_dir = settings.results_dir / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    df.to_csv(run_dir / "benchmark_results.csv", index=False)
    config = {
        "instances": instance_names,
        "time_limit_s": time_limit_s,
        "graph_cache_key": graph_report.cache_path.stem,
    }
    (run_dir / "config.yaml").write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")

    out_path = Path(out) if out else REPO_ROOT / "docs" / "report" / "benchmark_results.csv"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out_path, index=False)
    typer.echo(f"written to:        {run_dir}")
    typer.echo(f"also written to:   {out_path}")


if __name__ == "__main__":
    app()
