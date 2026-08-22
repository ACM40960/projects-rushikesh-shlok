"""Report-ready matplotlib figures: consistent style, colourblind-safe,
vector output, written to ``docs/report/figures/``.

Reads the aggregate CSV `dlm batch` produces (`dlm.cli.batch`,
``docs/report/batch_results.csv`` by default) — this module has no
dependency on the solver/disruption/simulation pipeline itself, only on
that CSV's columns, so figures can be regenerated (`dlm figures`) without
re-running any experiment.
"""

from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # headless: never opens a window, safe for CI/scripts

import matplotlib.pyplot as plt
import pandas as pd

# Okabe & Ito (2008) colourblind-safe palette.
_COLOR_T1 = "#0072B2"
_COLOR_T2 = "#D55E00"
_COLOR_T3 = "#009E73"
_COLOR_T3_ORACLE = "#CC79A7"
_COLOR_FEASIBLE = "#009E73"
_COLOR_INFEASIBLE = "#D55E00"

plt.rcParams.update(
    {
        "figure.dpi": 150,
        "savefig.dpi": 150,
        "font.size": 10,
        "axes.spines.top": False,
        "axes.spines.right": False,
        "axes.grid": True,
        "grid.alpha": 0.3,
    }
)


def _save_both(fig: plt.Figure, out_dir: Path, stem: str) -> tuple[Path, Path]:
    """Save `fig` as both PNG (easy to embed/screenshot) and SVG (true
    vector, for the report itself), returning `(png_path, svg_path)`."""
    out_dir.mkdir(parents=True, exist_ok=True)
    png_path = out_dir / f"{stem}.png"
    svg_path = out_dir / f"{stem}.svg"
    fig.savefig(png_path)
    fig.savefig(svg_path)
    plt.close(fig)
    return png_path, svg_path


def plot_curated_scenario_comparison(df: pd.DataFrame, out_dir: Path) -> tuple[Path, Path]:
    """Grouped bar chart: T1/T2(reactive)/T3/T3_oracle for each curated
    scenario. `df` should already be filtered to one instance — mixing
    instances would repeat scenario names on the x-axis meaninglessly.
    Infeasible values are omitted (no bar) and annotated, never plotted
    as a misleading zero.
    """
    curated = df[df["scenario_kind"] == "curated"].sort_values("scenario")
    labels = [value.replace("_", " ").title() for value in curated["scenario"]]
    metrics = [
        ("T1_total_s", "T1 (normal)", _COLOR_T1),
        ("T2_reactive_total_s", "T2 (reactive)", _COLOR_T2),
        ("T3_total_s", "T3 (re-optimised)", _COLOR_T3),
        ("T3_oracle_total_s", "T3 full-knowledge heuristic", _COLOR_T3_ORACLE),
    ]
    x = list(range(len(labels)))
    width = 0.2

    fig, ax = plt.subplots(figsize=(9, 5))
    for i, (col, label, color) in enumerate(metrics):
        values = curated[col]
        offsets = [xi + (i - 1.5) * width for xi in x]
        ax.bar(offsets, values.fillna(0.0), width=width, label=label, color=color)
        for xi, feasible in zip(offsets, values.notna(), strict=True):
            if not feasible:
                ax.annotate(
                    "infeasible",
                    (xi, 40),
                    rotation=90,
                    ha="center",
                    va="bottom",
                    fontsize=7,
                    color="dimgrey",
                )
    ax.set_xticks(x)
    ax.set_xticklabels(labels, rotation=20, ha="right")
    ax.set_ylabel("Total time (s)")
    instance_name = str(curated["instance"].iloc[0])
    ax.set_title(f"Route costs under curated disruptions — {instance_name} instance")
    ax.legend()
    fig.tight_layout()
    return _save_both(fig, out_dir, "t1_t2_t3_comparison")


def plot_feasibility_breakdown(df: pd.DataFrame, out_dir: Path) -> tuple[Path, Path]:
    """Stacked bar: feasible vs infeasible run counts per information
    model, across every (instance, scenario) pair in `df`."""
    metrics = [
        ("T2_omniscient_feasible", "T2\n(omniscient)"),
        ("T2_reactive_feasible", "T2\n(reactive)"),
        ("T3_feasible", "T3"),
        ("T3_oracle_feasible", "Full-knowledge\nheuristic"),
    ]
    labels = [m[1] for m in metrics]
    total = len(df)
    feasible_counts = [int(df[m[0]].sum()) for m in metrics]
    infeasible_counts = [total - c for c in feasible_counts]

    fig, ax = plt.subplots(figsize=(7, 4))
    ax.bar(labels, feasible_counts, color=_COLOR_FEASIBLE, label="feasible")
    ax.bar(
        labels,
        infeasible_counts,
        bottom=feasible_counts,
        color=_COLOR_INFEASIBLE,
        label="infeasible",
    )
    ax.set_ylabel(f"Runs (of {total})")
    ax.set_title("Feasibility by information model")
    ax.legend()
    fig.tight_layout()
    return _save_both(fig, out_dir, "feasibility_breakdown")


def plot_saving_distribution(df: pd.DataFrame, out_dir: Path) -> tuple[Path, Path]:
    """Count zero/positive/negative savings and infeasible comparisons."""
    values = df["saving_pct"].dropna()
    tolerance = 1e-9
    counts = [
        int((values.abs() <= tolerance).sum()),
        int((values > tolerance).sum()),
        int((values < -tolerance).sum()),
        int(df["saving_pct"].isna().sum()),
    ]
    labels = ["Zero saving", "Positive", "Negative", "Infeasible"]
    fig, ax = plt.subplots(figsize=(7, 4))
    bars = ax.bar(
        labels,
        counts,
        color=[_COLOR_T1, _COLOR_T3, _COLOR_T2, _COLOR_INFEASIBLE],
    )
    ax.bar_label(bars, padding=3)
    ax.set_ylabel("Runs")
    ax.set_title("Reactive reordering outcomes in the default batch")
    ax.set_ylim(0, max(counts, default=0) * 1.15 + 1)
    fig.tight_layout()
    return _save_both(fig, out_dir, "saving_distribution")


def plot_service_time_sensitivity(df: pd.DataFrame, out_dir: Path) -> tuple[Path, Path]:
    """T1 sensitivity to the assumed service time per stop."""
    fig, ax = plt.subplots(figsize=(7, 4.5))
    for instance_name, group in df.groupby("instance", sort=False):
        group = group.sort_values("default_service_time_s")
        ax.plot(
            group["default_service_time_s"],
            group["total_time_s"],
            marker="o",
            label=str(instance_name).title(),
        )
    ax.set_xlabel("Service time per stop (s)")
    ax.set_ylabel("T1 total time (s)")
    ax.set_title("Service-time sensitivity")
    ax.legend(title="Instance")
    fig.tight_layout()
    return _save_both(fig, out_dir, "service_time_sensitivity")


def plot_benchmark_gap(df: pd.DataFrame, out_dir: Path) -> tuple[Path, Path]:
    """Hand-solver percentage gap relative to the OR-Tools benchmark."""
    data = df.dropna(subset=["gap_pct"])
    labels = [str(value).title() for value in data["instance"]]
    fig, ax = plt.subplots(figsize=(7, 4.5))
    bars = ax.bar(labels, data["gap_pct"], color=_COLOR_T1)
    ax.bar_label(bars, labels=[f"{value:.1f}%" for value in data["gap_pct"]], padding=3)
    ax.set_ylabel("Gap to OR-Tools (%)")
    ax.set_title("Hand-implemented solver benchmark gap")
    ax.set_ylim(0, max(data["gap_pct"], default=0) * 1.18 + 1)
    fig.tight_layout()
    return _save_both(fig, out_dir, "benchmark_gap")


def plot_stress_test_saving(df: pd.DataFrame, out_dir: Path) -> tuple[Path, Path]:
    """Show the engineered stress test separately from the default batch."""
    data = df.dropna(subset=["saving_pct"])
    labels = [str(value).replace("_", " ").title() for value in data["scenario"]]
    fig, ax = plt.subplots(figsize=(7, 4.5))
    bars = ax.bar(labels, data["saving_pct"], color=_COLOR_T3)
    ax.bar_label(bars, labels=[f"{value:.1f}%" for value in data["saving_pct"]], padding=3)
    ax.set_ylabel("Reactive reordering saving (%)")
    ax.set_title("Route-intersection stress test")
    ax.set_ylim(0, max(data["saving_pct"], default=0) * 1.18 + 1)
    fig.tight_layout()
    return _save_both(fig, out_dir, "stress_test_saving")


def make_all_figures(
    results_csv: Path, out_dir: Path, instance: str | None = None
) -> list[tuple[Path, Path]]:
    """Read `results_csv` (`dlm batch`'s output) and write every report
    figure to `out_dir`. `instance` selects which instance's rows drive
    the per-scenario comparison figure (default: the first instance in
    the file); the feasibility/saving figures use every row regardless.
    """
    df = pd.read_csv(results_csv)
    if df.empty:
        raise ValueError(f"{results_csv} has no rows — run `dlm batch` first.")
    chosen_instance = instance or df["instance"].iloc[0]
    instance_df = df[df["instance"] == chosen_instance]

    figures = [
        plot_curated_scenario_comparison(instance_df, out_dir),
        plot_feasibility_breakdown(df, out_dir),
        plot_saving_distribution(df, out_dir),
    ]
    sensitivity_path = results_csv.with_name("sensitivity_results.csv")
    benchmark_path = results_csv.with_name("benchmark_results.csv")
    stress_path = results_csv.with_name("stress_test_results.csv")
    if sensitivity_path.exists():
        figures.append(plot_service_time_sensitivity(pd.read_csv(sensitivity_path), out_dir))
    if benchmark_path.exists():
        figures.append(plot_benchmark_gap(pd.read_csv(benchmark_path), out_dir))
    if stress_path.exists():
        figures.append(plot_stress_test_saving(pd.read_csv(stress_path), out_dir))
    return figures


__all__ = [
    "make_all_figures",
    "plot_curated_scenario_comparison",
    "plot_feasibility_breakdown",
    "plot_benchmark_gap",
    "plot_saving_distribution",
    "plot_service_time_sensitivity",
    "plot_stress_test_saving",
]
