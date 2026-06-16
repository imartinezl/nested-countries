"""Reading/writing edges, placements, chains and reproducibility sidecars.

Edge computation is expensive, so edges are written *incrementally* to CSV and
the search settings are stored in a sidecar JSON next to them. On resume, pairs
already present are skipped unless ``force`` is requested.
"""

from __future__ import annotations

import csv
import json
import logging
from dataclasses import asdict
from pathlib import Path

import pandas as pd

from .graph_search import ChainResult
from .models import Placement, SearchSettings

log = logging.getLogger("nested_countries.io")

EDGE_FIELDS = [
    "outer_iso", "inner_iso", "outer_name", "inner_name",
    "valid", "status", "confidence", "angle_deg", "dx", "dy",
    "clearance_km", "method", "reason",
]


# ---------------------------------------------------------------------------
# Settings sidecar
# ---------------------------------------------------------------------------
def settings_sidecar_path(output_csv: str | Path) -> Path:
    p = Path(output_csv)
    return p.with_suffix(p.suffix + ".settings.json")


def write_settings_sidecar(output_csv: str | Path, settings: SearchSettings) -> Path:
    side = settings_sidecar_path(output_csv)
    side.parent.mkdir(parents=True, exist_ok=True)
    side.write_text(json.dumps(settings.to_dict(), indent=2), encoding="utf-8")
    return side


def read_settings_sidecar(output_csv: str | Path) -> dict | None:
    side = settings_sidecar_path(output_csv)
    if side.exists():
        return json.loads(side.read_text(encoding="utf-8"))
    return None


# ---------------------------------------------------------------------------
# Incremental edge CSV (cache / resume)
# ---------------------------------------------------------------------------
def load_existing_pairs(output_csv: str | Path) -> set[tuple[str, str]]:
    """Return the set of (outer_iso, inner_iso) pairs already computed."""
    path = Path(output_csv)
    if not path.exists():
        return set()
    try:
        df = pd.read_csv(path)
    except Exception:
        return set()
    if "outer_iso" not in df.columns or "inner_iso" not in df.columns:
        return set()
    return {
        (str(r.outer_iso), str(r.inner_iso))
        for r in df.itertuples(index=False)
    }


class EdgeWriter:
    """Append-only CSV writer that creates the header on first use."""

    def __init__(self, output_csv: str | Path, resume: bool = True):
        self.path = Path(output_csv)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._new = not (resume and self.path.exists() and self.path.stat().st_size > 0)
        mode = "w" if self._new else "a"
        self._fh = self.path.open(mode, newline="", encoding="utf-8")
        self._writer = csv.DictWriter(self._fh, fieldnames=EDGE_FIELDS)
        if self._new:
            self._writer.writeheader()
            self._fh.flush()

    def write(self, placement: Placement) -> None:
        row = {k: placement.as_row().get(k) for k in EDGE_FIELDS}
        self._writer.writerow(row)
        self._fh.flush()  # flush so a crash still leaves a resumable file

    def close(self) -> None:
        self._fh.close()

    def __enter__(self) -> "EdgeWriter":
        return self

    def __exit__(self, *exc) -> None:
        self.close()


def load_edges(output_csv: str | Path) -> pd.DataFrame:
    """Load an edges CSV, tolerating the occasional malformed row.

    The incremental writer can, very rarely, leave a single corrupted line
    (e.g. on an interrupted flush). Such rows are skipped with a warning rather
    than failing the whole run; they are non-edges in practice.
    """
    try:
        return pd.read_csv(output_csv)
    except pd.errors.ParserError:
        log.warning("Malformed line(s) in %s; skipping them.", output_csv)
        return pd.read_csv(output_csv, engine="python", on_bad_lines="skip")


# ---------------------------------------------------------------------------
# Placements (e.g. for a verified chain)
# ---------------------------------------------------------------------------
def save_placements_csv(placements: list[Placement], path: str | Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    df = pd.DataFrame([p.as_row() for p in placements])
    df.to_csv(path, index=False)
    log.info("Saved %d placements -> %s", len(placements), path)
    return path


# ---------------------------------------------------------------------------
# Chains
# ---------------------------------------------------------------------------
def save_chains(chains: list[ChainResult], path: str | Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = [asdict(c) for c in chains]
    if path.suffix.lower() == ".json":
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    else:  # CSV: one row per chain
        rows = [
            {
                "rank": i + 1,
                "length": c.length,
                "min_clearance_km": c.min_clearance_km,
                "chain": " -> ".join(c.names),
                "iso_chain": " -> ".join(c.nodes),
            }
            for i, c in enumerate(chains)
        ]
        pd.DataFrame(rows).to_csv(path, index=False)
    log.info("Saved %d chains -> %s", len(chains), path)
    return path
