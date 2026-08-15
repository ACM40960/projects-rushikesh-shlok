"""Stage 0 smoke tests: the package imports, settings load, and the CLI
entry point exists. Everything downstream depends on these holding."""

from __future__ import annotations

import typer

from dlm import __version__
from dlm.cli import app
from dlm.config import DISTANCE_UNIT, TIME_UNIT, settings


def test_version_is_set() -> None:
    assert __version__ == "0.1.0"


def test_settings_defaults() -> None:
    assert settings.seed == 42
    assert settings.log_level == "INFO"
    assert settings.data_dir.name == "data"
    assert settings.cache_dir.name == "cache"


def test_units_policy() -> None:
    assert TIME_UNIT == "seconds"
    assert DISTANCE_UNIT == "metres"


def test_cli_app_is_typer_app() -> None:
    assert isinstance(app, typer.Typer)
