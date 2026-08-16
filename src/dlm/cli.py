"""``dlm`` — the command-line entry point for the whole pipeline.

The architectural rule (§2.1 of the project brief) is that the UI is a thin
client: every capability the Streamlit app exposes must already exist as a
CLI command here. Sub-commands are added stage by stage as their underlying
modules land:

- Stage 1 adds ``dlm network build`` / ``dlm network stats``.
- Stage 2 adds ``dlm instance new`` / ``add`` / ``remove`` / ``random`` / ``list`` / ``show`` /
  ``map``.
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


if __name__ == "__main__":
    app()
