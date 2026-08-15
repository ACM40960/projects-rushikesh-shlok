"""Global configuration: paths, seed, units policy, logging level.

Every setting has a sane default and can be overridden by an environment
variable prefixed ``DLM_`` (see ``.env.example``), or by a ``.env`` file in
the repository root. This is the single place downstream modules read
paths and the default random seed from — nothing else should hard-code a
path or a seed.

Units policy (fixed, not configurable): time is always **seconds**,
distance is always **metres**, coordinates are always **(lat, lon)** in
WGS84 decimal degrees unless a function name says otherwise (e.g. GeoJSON
geometries use ``[lon, lat]`` per the GeoJSON spec — this is called out
explicitly wherever it applies).
"""

from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]

TIME_UNIT = "seconds"
DISTANCE_UNIT = "metres"


class Settings(BaseSettings):
    """Project-wide settings, loaded from env vars (prefix ``DLM_``) or ``.env``.

    Attributes
    ----------
    data_dir : Path
        Root of committed and cached data (``data/``).
    cache_dir : Path
        Gitignored cache for graphs, matrices, geocoding (``data/cache/``).
    presets_dir : Path
        Curated Dublin location presets (``data/presets/``).
    instances_dir : Path
        Committed problem instances used in the report (``data/instances/``).
    results_dir : Path
        Gitignored per-run outputs (``results/``).
    scenarios_dir : Path
        Version-controlled disruption scenario YAML files (``scenarios/``).
    seed : int
        Default global random seed. Every stochastic operation must accept
        an explicit seed; this is only the default when the caller omits one.
    log_level : str
        Python logging level name, e.g. ``INFO``, ``DEBUG``.
    """

    model_config = SettingsConfigDict(
        env_prefix="DLM_",
        env_file=str(REPO_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    data_dir: Path = Field(default=REPO_ROOT / "data")
    cache_dir: Path = Field(default=REPO_ROOT / "data" / "cache")
    presets_dir: Path = Field(default=REPO_ROOT / "data" / "presets")
    instances_dir: Path = Field(default=REPO_ROOT / "data" / "instances")
    results_dir: Path = Field(default=REPO_ROOT / "results")
    scenarios_dir: Path = Field(default=REPO_ROOT / "scenarios")

    seed: int = Field(default=42)
    log_level: str = Field(default="INFO")

    def ensure_dirs(self) -> None:
        """Create all configured directories if they do not already exist."""
        for d in (
            self.data_dir,
            self.cache_dir,
            self.presets_dir,
            self.instances_dir,
            self.results_dir,
        ):
            d.mkdir(parents=True, exist_ok=True)


settings = Settings()
