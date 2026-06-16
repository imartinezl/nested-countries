"""Central paths, defaults and the known candidate chain.

Everything here is import-time cheap so any script can ``from nested_countries
import config`` without pulling in heavy geospatial deps.
"""

from __future__ import annotations

import logging
from pathlib import Path

# ---------------------------------------------------------------------------
# Project layout
# ---------------------------------------------------------------------------
# config.py lives at src/nested_countries/config.py -> repo root is parents[2].
PACKAGE_DIR = Path(__file__).resolve().parent
REPO_ROOT = PACKAGE_DIR.parents[1]

DATA_DIR = REPO_ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
NATURAL_EARTH_DIR = RAW_DIR / "natural_earth"
PROCESSED_DIR = DATA_DIR / "processed"

OUTPUTS_DIR = REPO_ROOT / "outputs"
EDGES_DIR = OUTPUTS_DIR / "edges"
CHAINS_DIR = OUTPUTS_DIR / "chains"
PLACEMENTS_DIR = OUTPUTS_DIR / "placements"
DASHBOARD_DIR = OUTPUTS_DIR / "dashboard"
FIGURES_DIR = OUTPUTS_DIR / "figures"

TEMPLATES_DIR = REPO_ROOT / "templates"

# ---------------------------------------------------------------------------
# Search defaults
# ---------------------------------------------------------------------------
DEFAULT_EPSILON_KM = 1.0
DEFAULT_ANGLE_STEP_DEG = 5.0
DEFAULT_GRID_STEP_KM = 25.0
DEFAULT_MODE = "mainland"
DEFAULT_AREA_THRESHOLD = 0.01

# ---------------------------------------------------------------------------
# Dataset
# ---------------------------------------------------------------------------
DATASET_NAME = "Natural Earth Admin 0 Countries (1:10m)"
NE_LAYER_BASENAME = "ne_10m_admin_0_countries"

# Mirrors are tried in order. Each entry is (label, url). The first that yields
# a readable layer wins. Adding new mirrors here is the only change needed if
# upstream URLs move.
NATURAL_EARTH_SOURCES = [
    (
        "naciscdn zip (1:10m)",
        "https://naciscdn.org/naturalearth/10m/cultural/ne_10m_admin_0_countries.zip",
    ),
    (
        "naturalearth.s3 zip (1:10m)",
        "https://naturalearth.s3.amazonaws.com/10m_cultural/ne_10m_admin_0_countries.zip",
    ),
    (
        "github nvkelso geojson (1:10m)",
        "https://github.com/nvkelso/natural-earth-vector/raw/master/geojson/"
        "ne_10m_admin_0_countries.geojson",
    ),
]

# ---------------------------------------------------------------------------
# The known 13-country candidate chain to verify.
# ---------------------------------------------------------------------------
KNOWN_CHAIN = [
    "Russia",
    "India",
    "Libya",
    "Turkey",
    "Finland",
    "Nepal",
    "Togo",
    "Qatar",
    "Luxembourg",
    "Barbados",
    "Liechtenstein",
    "Monaco",
    "Vatican City",
]


def ensure_dirs() -> None:
    """Create the output / data directory tree if it does not yet exist."""
    for d in (
        RAW_DIR,
        NATURAL_EARTH_DIR,
        PROCESSED_DIR,
        EDGES_DIR,
        CHAINS_DIR,
        PLACEMENTS_DIR,
        DASHBOARD_DIR,
        FIGURES_DIR,
    ):
        d.mkdir(parents=True, exist_ok=True)


def setup_logging(level: int = logging.INFO) -> logging.Logger:
    """Configure a simple, readable console logger and return the project logger."""
    logging.basicConfig(
        level=level,
        format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
        datefmt="%H:%M:%S",
    )
    return logging.getLogger("nested_countries")
