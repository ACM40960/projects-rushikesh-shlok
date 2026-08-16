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
- Stage 5 adds ``dlm disrupt validate`` / ``preview`` / ``new``.
- Stage 6 adds ``dlm compare``.
- Stage 7 adds ``dlm batch``.

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
    fleet_size: int = typer.Option(1, "--fleet-size", min=1),
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
    builder = InstanceBuilder(graph, name=name, seed=seed, fleet_size=fleet_size)
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
            result = builder.add_stop_from_address(address, label=label)
        elif latlon is not None:
            lat, lon = _parse_latlon(latlon)
            result = builder.add_stop_from_latlon(lat, lon, label=label)
        else:
            assert preset is not None
            result = builder.add_stop_from_preset(preset)
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


@app.command("plan")
def plan(
    instance: str = typer.Option(..., "--instance", help="Instance name."),
    solver: str = typer.Option("nn_2opt", "--solver", help="'nn_2opt' or 'nearest_neighbour'."),
) -> None:
    """Solve an instance's baseline route and report T1.

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

    nodes = [inst.depot.node, *(s.node for s in inst.stops)]
    matrix, _ = build_matrix(graph, nodes, graph_id=graph_report.cache_path.stem)

    solvers = {"nn_2opt": TwoOptSolver(), "nearest_neighbour": NearestNeighbourSolver()}
    if solver not in solvers:
        typer.echo(f"Unknown solver {solver!r}. Choices: {', '.join(solvers)}")
        raise typer.Exit(code=1)
    solution = solvers[solver].solve(inst, matrix)
    t1 = compute_t1(inst, solution)

    run_id = f"{instance}-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}"
    run_dir = settings.results_dir / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

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


if __name__ == "__main__":
    app()
