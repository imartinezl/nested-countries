"""Build the static interactive dashboard at outputs/dashboard/index.html."""

from __future__ import annotations

import argparse
from pathlib import Path

import _bootstrap  # noqa: F401

from nested_countries import config
from nested_countries.dashboard import build_dashboard
from nested_countries.data import load_country_shapes
from nested_countries.io import read_settings_sidecar

log = config.setup_logging()


def main() -> int:
    ap = argparse.ArgumentParser(description="Build the static interactive dashboard.")
    ap.add_argument("--countries", required=True,
                    help="Processed countries file (.gpkg/.parquet) with geometries.")
    ap.add_argument("--edges", default=None, help="Edge CSV (default: first under outputs/edges).")
    ap.add_argument("--top-n", type=int, default=12, help="How many top chains to feature.")
    ap.add_argument("--output", default=str(config.DASHBOARD_DIR / "index.html"))
    args = ap.parse_args()

    config.ensure_dirs()

    edges = args.edges
    if edges is None:
        found = sorted(config.EDGES_DIR.glob("*.csv"))
        edges = str(found[0]) if found else None
        if edges:
            log.info("Using edges: %s", edges)
    if not edges or not Path(edges).exists():
        log.error("No edges CSV found. Run scripts/compute_edges.py first.")
        return 2

    registry = load_country_shapes(args.countries)
    settings = read_settings_sidecar(edges)

    out = build_dashboard(
        registry=registry,
        edges_csv=edges,
        output_html=Path(args.output),
        settings=settings,
        top_n=args.top_n,
    )
    print(f"\nOpen: {out}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
