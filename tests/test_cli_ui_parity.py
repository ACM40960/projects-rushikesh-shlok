"""CLI/UI parity tests — added in Stage 10 (see docs/stages/stage-10-ui.md).

Asserts an instance/scenario built through the UI's underlying function
calls (`app.state.run_plan`/`run_compare`) produces exactly the same
`T1`/`T2`/`T3`/`Saving %` as the equivalent `dlm plan`/`dlm compare` CLI
invocation, using `typer.testing.CliRunner` to run the real CLI commands
in-process (no subprocess) and reading the same `result.json` a real `dlm`
invocation writes. This is the concrete check for the architectural law
in `docs/architecture.md`: the UI is a thin client, never a second
implementation of routing/solving/disruption/metric logic.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import app.state as ui_state
import pytest
from typer.testing import CliRunner

from dlm.cli import app as cli_app

runner = CliRunner()


def _result_json_from_output(stdout: str) -> dict:
    match = re.search(r"written to:\s*(\S+)", stdout)
    assert match, f"could not find 'written to:' in CLI output:\n{stdout}"
    return json.loads((Path(match.group(1)) / "result.json").read_text())


@pytest.mark.network
def test_plan_parity_single_vehicle():
    result = runner.invoke(cli_app, ["plan", "--instance", "small"])
    assert result.exit_code == 0, result.output
    cli_result = _result_json_from_output(result.output)

    ui_outcome = ui_state.run_plan("small")

    assert ui_outcome.t1.drive_time_s == pytest.approx(cli_result["T1"]["drive_time_s"])
    assert ui_outcome.t1.service_time_s == pytest.approx(cli_result["T1"]["service_time_s"])
    assert ui_outcome.t1.total_time_s == pytest.approx(cli_result["T1"]["total_time_s"])
    assert ui_outcome.solution.order == cli_result["order"]


@pytest.mark.network
def test_plan_parity_fleet():
    result = runner.invoke(cli_app, ["plan", "--instance", "fleet"])
    assert result.exit_code == 0, result.output
    cli_result = _result_json_from_output(result.output)

    ui_outcome = ui_state.run_plan("fleet")

    assert ui_outcome.t1.total_time_s == pytest.approx(cli_result["T1"]["total_time_s"])
    assert ui_outcome.t1.n_stops_served == cli_result["T1"]["n_stops_served"]
    assert ui_outcome.solver_name == "clarke_wright_2opt"
    assert len(ui_outcome.fleet.routes) == cli_result["n_vehicles_used"]


@pytest.mark.network
def test_compare_parity():
    result = runner.invoke(
        cli_app,
        ["compare", "--instance", "small", "--scenario", "luas_works_dawson_street"],
    )
    assert result.exit_code == 0, result.output
    cli_result = _result_json_from_output(result.output)

    ui_outcome = ui_state.run_compare("small", "luas_works_dawson_street")

    assert ui_outcome.t1.total_time_s == pytest.approx(cli_result["T1"]["total_time_s"])
    assert ui_outcome.t2_omniscient.feasible == cli_result["T2_omniscient"]["feasible"]
    assert ui_outcome.t2_omniscient.total_time_s == pytest.approx(
        cli_result["T2_omniscient"]["total_time_s"]
    )
    assert ui_outcome.t2_reactive.feasible == cli_result["T2_reactive"]["feasible"]
    assert ui_outcome.t2_reactive.total_time_s == pytest.approx(
        cli_result["T2_reactive"]["total_time_s"]
    )
    assert ui_outcome.t3.feasible == cli_result["T3"]["feasible"]
    assert ui_outcome.t3.total_time_s == pytest.approx(cli_result["T3"]["total_time_s"])
    assert ui_outcome.t3.triggered == cli_result["T3"]["triggered"]
    assert ui_outcome.t3_oracle.total_time_s == pytest.approx(
        cli_result["T3_oracle"]["total_time_s"]
    )
    if ui_outcome.saving_pct is None:
        assert cli_result["saving_pct"] is None
    else:
        assert ui_outcome.saving_pct == pytest.approx(cli_result["saving_pct"])


@pytest.mark.network
def test_compare_rejects_fleet_instance_same_as_cli_would():
    """`dlm compare` has no `fleet_size > 1` support at all (it's not in
    its own code path); the UI's `run_compare` raises the same
    "unsupported" outcome explicitly rather than silently doing the
    wrong thing."""
    with pytest.raises(ValueError, match="fleet_size"):
        ui_state.run_compare("fleet", "luas_works_dawson_street")
