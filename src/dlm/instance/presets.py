"""Curated Dublin location presets (name -> lat/lon/category).

Backed by ``data/presets/dublin_locations.yaml``. This is what makes demos
fast and the report's instances legible — a stop can be added by a
recognisable name ("Mater Hospital") instead of raw coordinates.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel

from dlm.config import settings

_PRESETS_FILENAME = "dublin_locations.yaml"


class Preset(BaseModel):
    """One curated location.

    Attributes
    ----------
    name : str
        Display name, used for lookup (case-insensitive, exact match).
    lat, lon : float
        WGS84 decimal degrees — already chosen to be near a real drivable
        road (see the note at the top of ``dublin_locations.yaml`` for the
        handful of locations where the geocoded centroid itself was not).
    category : str
        One of: hospital, university, retail, suburb, transport_hub, landmark.
    """

    name: str
    lat: float
    lon: float
    category: str


class PresetNotFoundError(KeyError):
    """Raised when a preset name has no match."""

    def __init__(self, name: str, available: list[str]) -> None:
        self.name = name
        self.available = available
        super().__init__(f"No preset named {name!r}. Available: {', '.join(sorted(available))}")


def _presets_path() -> Path:
    return settings.presets_dir / _PRESETS_FILENAME


def load_presets() -> list[Preset]:
    """Load all curated presets from ``data/presets/dublin_locations.yaml``."""
    raw = yaml.safe_load(_presets_path().read_text(encoding="utf-8"))
    return [Preset(**entry) for entry in raw]


def get_preset(name: str) -> Preset:
    """Look up a preset by name (case-insensitive, exact match).

    Raises
    ------
    PresetNotFoundError
        If no preset matches `name`; lists the available names.
    """
    presets = load_presets()
    for p in presets:
        if p.name.lower() == name.lower():
            return p
    raise PresetNotFoundError(name, [p.name for p in presets])
