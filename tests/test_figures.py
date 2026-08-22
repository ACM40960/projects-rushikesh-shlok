"""Tests for dlm.viz.figures — added in Stage 7.

All offline: `dlm.viz.figures` only ever reads a `dlm batch`-shaped CSV
(see its module docstring), so these tests build that CSV directly with
pandas rather than running the real pipeline — fast, and exercises the
edge cases (infeasible rows, an all-infeasible saving column, a single
instance) that a full batch run may or may not happen to hit.
"""

from __future__ import annotations

import math

import pandas as pd
import pytest

from dlm.viz.figures import (
    make_all_figures,
    plot_benchmark_gap,
    plot_curated_scenario_comparison,
    plot_feasibility_breakdown,
    plot_saving_distribution,
    plot_service_time_sensitivity,
    plot_stress_test_saving,
)

_COLUMNS = [
    "instance",
    "n_stops",
    "scenario",
    "scenario_kind",
    "shape",
    "effect",
    "edges_closed",
    "edges_slowed",
    "T1_total_s",
    "T2_omniscient_feasible",
    "T2_omniscient_total_s",
    "T2_reactive_feasible",
    "T2_reactive_total_s",
    "T3_feasible",
    "T3_triggered",
    "T3_total_s",
    "T3_oracle_feasible",
    "T3_oracle_total_s",
    "saving_pct",
]


def _row(
    instance="small",
    scenario="scenario_a",
    kind="curated",
    shape="corridor",
    effect="closure",
    t1=1000.0,
    t2_feasible=True,
    t2=1050.0,
    t3_feasible=True,
    t3_triggered=True,
    t3=1020.0,
    t3_oracle_feasible=True,
    t3_oracle=1010.0,
    saving=None,
) -> dict:
    if saving is None and t2_feasible and t3_feasible:
        saving = (t2 - t3) / t2 * 100
    return {
        "instance": instance,
        "n_stops": 8,
        "scenario": scenario,
        "scenario_kind": kind,
        "shape": shape,
        "effect": effect,
        "edges_closed": 10,
        "edges_slowed": 0,
        "T1_total_s": t1,
        "T2_omniscient_feasible": t2_feasible,
        "T2_omniscient_total_s": t2 if t2_feasible else math.nan,
        "T2_reactive_feasible": t2_feasible,
        "T2_reactive_total_s": t2 if t2_feasible else math.nan,
        "T3_feasible": t3_feasible,
        "T3_triggered": t3_triggered,
        "T3_total_s": t3 if t3_feasible else math.nan,
        "T3_oracle_feasible": t3_oracle_feasible,
        "T3_oracle_total_s": t3_oracle if t3_oracle_feasible else math.nan,
        "saving_pct": saving,
    }


def _make_df(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=_COLUMNS)


def test_make_all_figures_writes_png_and_svg_for_each_figure(tmp_path) -> None:
    df = _make_df(
        [
            _row(scenario="a", saving=5.0),
            _row(scenario="b", kind="random", t2=1100.0, t3=1080.0),
            _row(scenario="c", t2_feasible=False, t3_feasible=False, saving=None),
        ]
    )
    csv_path = tmp_path / "batch_results.csv"
    df.to_csv(csv_path, index=False)

    written = make_all_figures(csv_path, tmp_path / "figures")

    assert len(written) == 3
    for png_path, svg_path in written:
        assert png_path.exists() and png_path.stat().st_size > 0
        assert svg_path.exists() and svg_path.stat().st_size > 0
        assert png_path.suffix == ".png"
        assert svg_path.suffix == ".svg"


def test_make_all_figures_raises_on_an_empty_csv(tmp_path) -> None:
    csv_path = tmp_path / "empty.csv"
    _make_df([]).to_csv(csv_path, index=False)
    with pytest.raises(ValueError, match="no rows"):
        make_all_figures(csv_path, tmp_path / "figures")


def test_curated_comparison_handles_every_scenario_infeasible(tmp_path) -> None:
    df = _make_df(
        [
            _row(scenario="a", t2_feasible=False, t3_feasible=False, t3_oracle_feasible=False),
            _row(scenario="b", t2_feasible=False, t3_feasible=False, t3_oracle_feasible=False),
        ]
    )
    png_path, svg_path = plot_curated_scenario_comparison(df, tmp_path)
    assert png_path.exists() and png_path.stat().st_size > 0


def test_feasibility_breakdown_handles_mixed_feasibility(tmp_path) -> None:
    df = _make_df(
        [
            _row(t2_feasible=True, t3_feasible=True),
            _row(t2_feasible=False, t3_feasible=False, t3_oracle_feasible=False),
        ]
    )
    png_path, _svg_path = plot_feasibility_breakdown(df, tmp_path)
    assert png_path.exists() and png_path.stat().st_size > 0


def test_saving_distribution_handles_no_feasible_pairs(tmp_path) -> None:
    df = _make_df(
        [
            _row(t2_feasible=False, t3_feasible=False, saving=None),
            _row(t2_feasible=False, t3_feasible=False, saving=None),
        ]
    )
    assert df["saving_pct"].isna().all()
    png_path, _svg_path = plot_saving_distribution(df, tmp_path)
    assert png_path.exists() and png_path.stat().st_size > 0


def test_make_all_figures_uses_the_first_instance_by_default(tmp_path) -> None:
    df = _make_df(
        [
            _row(instance="small", scenario="a"),
            _row(instance="medium", scenario="b"),
        ]
    )
    csv_path = tmp_path / "batch_results.csv"
    df.to_csv(csv_path, index=False)

    # both should succeed regardless of which instance drives the comparison figure
    make_all_figures(csv_path, tmp_path / "default")
    make_all_figures(csv_path, tmp_path / "explicit", instance="medium")


def test_sensitivity_benchmark_and_stress_figures(tmp_path) -> None:
    sensitivity = pd.DataFrame(
        {
            "instance": ["small", "small", "large", "large"],
            "default_service_time_s": [60, 180, 60, 180],
            "total_time_s": [1000, 1500, 3000, 5000],
        }
    )
    benchmark = pd.DataFrame({"instance": ["small", "large"], "gap_pct": [1.5, 15.8]})
    stress = pd.DataFrame({"scenario": ["demo_saving_showcase"], "saving_pct": [7.425946]})

    outputs = [
        plot_service_time_sensitivity(sensitivity, tmp_path),
        plot_benchmark_gap(benchmark, tmp_path),
        plot_stress_test_saving(stress, tmp_path),
    ]
    for png_path, svg_path in outputs:
        assert png_path.exists() and png_path.stat().st_size > 0
        assert svg_path.exists() and svg_path.stat().st_size > 0
