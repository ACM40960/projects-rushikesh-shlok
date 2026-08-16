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


if __name__ == "__main__":
    app()
