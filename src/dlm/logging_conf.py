"""Structured logging setup, shared by the CLI, the app, and batch experiments.

Call :func:`configure_logging` once, as early as possible (the CLI entry
point and the Streamlit app both do this). Library code should never call
``logging.basicConfig`` itself — it should just do ``logging.getLogger(__name__)``.
"""

from __future__ import annotations

import logging
import sys

from dlm.config import settings

_LOG_FORMAT = "%(asctime)s %(levelname)-8s %(name)s: %(message)s"
_DATE_FORMAT = "%Y-%m-%dT%H:%M:%S%z"


def configure_logging(level: str | None = None) -> None:
    """Configure the root logger with a structured, single-line format.

    Parameters
    ----------
    level : str, optional
        Logging level name (e.g. ``"DEBUG"``, ``"INFO"``). Defaults to
        ``settings.log_level``, which is read from the ``DLM_LOG_LEVEL``
        environment variable.
    """
    resolved_level = (level or settings.log_level).upper()
    logging.basicConfig(
        level=resolved_level,
        format=_LOG_FORMAT,
        datefmt=_DATE_FORMAT,
        stream=sys.stderr,
        force=True,
    )
