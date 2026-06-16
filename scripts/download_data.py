"""Download Natural Earth Admin 0 Countries (1:10m), or explain manual setup.

Tries each mirror in ``config.NATURAL_EARTH_SOURCES`` in turn and verifies the
result is actually readable by geopandas. If every mirror fails it prints clear
instructions for placing the file by hand - it never fails mysteriously.
"""

from __future__ import annotations

import argparse
import io
import logging
import urllib.request
import zipfile
from pathlib import Path

import _bootstrap  # noqa: F401  (sys.path shim)

from nested_countries import config

log = config.setup_logging()

USER_AGENT = "nested-countries/0.1 (+https://example.org)"


def _download(url: str, timeout: int = 120) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def _save_payload(url: str, payload: bytes, dest_dir: Path) -> Path:
    """Persist a downloaded payload, extracting zips. Return a readable path."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    if url.lower().endswith(".zip") or payload[:2] == b"PK":
        with zipfile.ZipFile(io.BytesIO(payload)) as zf:
            zf.extractall(dest_dir)
        shps = sorted(dest_dir.glob("*.shp"))
        if not shps:
            raise IOError("zip extracted but no .shp inside")
        return shps[0]
    # GeoJSON / single file
    suffix = ".geojson" if "json" in url.lower() else Path(url).suffix or ".dat"
    out = dest_dir / f"{config.NE_LAYER_BASENAME}{suffix}"
    out.write_bytes(payload)
    return out


def _verify(path: Path) -> int:
    """Return the feature count if geopandas can read the layer."""
    import geopandas as gpd

    gdf = gpd.read_file(path)
    return len(gdf)


def main() -> int:
    ap = argparse.ArgumentParser(description="Download Natural Earth Admin 0 Countries (1:10m).")
    ap.add_argument("--dest", default=str(config.NATURAL_EARTH_DIR),
                    help="Destination directory (default: data/raw/natural_earth).")
    ap.add_argument("--source", default=None, help="Override mirror URL.")
    ap.add_argument("--force", action="store_true", help="Re-download even if data exists.")
    args = ap.parse_args()

    config.ensure_dirs()
    dest = Path(args.dest)

    existing = sorted(dest.glob("*.shp")) + sorted(dest.glob("*.geojson")) + sorted(dest.glob("*.gpkg"))
    if existing and not args.force:
        log.info("Data already present: %s (use --force to re-download).", existing[0])
        return 0

    sources = [("override", args.source)] if args.source else config.NATURAL_EARTH_SOURCES
    for label, url in sources:
        try:
            log.info("Downloading from %s: %s", label, url)
            payload = _download(url)
            path = _save_payload(url, payload, dest)
            n = _verify(path)
            log.info("SUCCESS: %d countries -> %s", n, path)
            return 0
        except Exception as exc:
            log.warning("Mirror failed (%s): %s", label, exc)

    _print_manual_instructions(dest)
    return 1


def _print_manual_instructions(dest: Path) -> None:
    log.error("All download mirrors failed.")
    print(
        "\n" + "=" * 70 + "\n"
        "MANUAL SETUP\n"
        "Download 'Admin 0 - Countries' (1:10m) from Natural Earth:\n"
        "  https://www.naturalearthdata.com/downloads/10m-cultural-vectors/\n"
        "or the GitHub mirror:\n"
        "  https://github.com/nvkelso/natural-earth-vector\n\n"
        f"Unzip so that this file exists:\n"
        f"  {dest / (config.NE_LAYER_BASENAME + '.shp')}\n"
        "(a .geojson or .gpkg of the same layer also works)\n"
        + "=" * 70 + "\n"
    )


if __name__ == "__main__":
    raise SystemExit(main())
