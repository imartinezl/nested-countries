"""Load raw country data and write processed, locally-projected geometries."""

from __future__ import annotations

import argparse
from pathlib import Path

import _bootstrap  # noqa: F401

from nested_countries import config
from nested_countries.data import build_country_shapes, load_raw_countries, save_country_shapes

log = config.setup_logging()


def main() -> int:
    ap = argparse.ArgumentParser(description="Preprocess country geometries.")
    ap.add_argument("--input", default=None,
                    help="Raw vector file (.shp/.gpkg/.geojson/.zip). Default: auto-discover.")
    ap.add_argument("--mode", choices=["mainland", "full", "area_threshold"],
                    default=config.DEFAULT_MODE, help="Geometry mode (default: mainland).")
    ap.add_argument("--area-threshold", type=float, default=config.DEFAULT_AREA_THRESHOLD,
                    help="For area_threshold mode: min component area as a fraction of the largest.")
    ap.add_argument("--proj", choices=["laea", "aeqd"], default="laea",
                    help="Local projection family (default: laea = equal-area).")
    ap.add_argument("--min-area-km2", type=float, default=0.0,
                    help="Drop countries smaller than this projected area.")
    ap.add_argument("--output", default=None,
                    help="Output .gpkg or .parquet. Default: data/processed/countries_<mode>.gpkg")
    args = ap.parse_args()

    config.ensure_dirs()
    output = Path(args.output) if args.output else config.PROCESSED_DIR / f"countries_{args.mode}.gpkg"

    gdf = load_raw_countries(args.input)
    log.info("Loaded %d raw countries.", len(gdf))
    shapes = build_country_shapes(
        gdf, mode=args.mode, area_threshold=args.area_threshold,
        proj_kind=args.proj, min_area_km2=args.min_area_km2,
    )
    save_country_shapes(shapes, output)
    log.info("Done: %d countries -> %s", len(shapes), output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
