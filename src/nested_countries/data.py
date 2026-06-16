"""Loading raw country data, robust name matching, and building CountryShapes.

The raw source is Natural Earth Admin 0 Countries. Its attribute columns are
upper-case (``NAME``, ``ISO_A3`` ...) and a handful of rows carry sentinel
``-99`` ISO codes, so matching is done against several name/ISO columns plus an
alias table.

Processed output is a set of *planar, kilometre-scale* geometries (each country
projected in its own local CRS and recentred near the origin). They are stored
in a GeoPackage/GeoParquet with ``crs=None`` because every row lives in a
different local projection - the geometry is meant to be rotated/translated
freely, not re-projected.
"""

from __future__ import annotations

import logging
import unicodedata
from pathlib import Path

import geopandas as gpd
import pandas as pd
from shapely.geometry.base import BaseGeometry

from . import config
from .geometry_modes import Mode, apply_mode, fix_geometry
from .models import CountryShape
from .projections import ProjKind, project_to_local_km

log = logging.getLogger("nested_countries.data")

# ---------------------------------------------------------------------------
# Candidate attribute columns (case-insensitive lookup).
# ---------------------------------------------------------------------------
NAME_COLUMNS = ["NAME", "NAME_LONG", "NAME_EN", "ADMIN", "SOVEREIGNT", "FORMAL_EN", "GEOUNIT"]
ISO_COLUMNS = ["ISO_A3_EH", "ISO_A3", "ADM0_A3", "SOV_A3", "GU_A3"]

# ---------------------------------------------------------------------------
# Aliases. Keys are *normalized* query strings (see ``normalize_name``); values
# are the ISO_A3 code, used as a strong fallback when the literal name differs
# between the user's spelling and the Natural Earth label.
# ---------------------------------------------------------------------------
ALIASES_TO_ISO: dict[str, str] = {
    "russia": "RUS",
    "russian federation": "RUS",
    "india": "IND",
    "libya": "LBY",
    "turkey": "TUR",
    "turkiye": "TUR",
    "republic of turkey": "TUR",
    "finland": "FIN",
    "nepal": "NPL",
    "togo": "TGO",
    "qatar": "QAT",
    "luxembourg": "LUX",
    "barbados": "BRB",
    "liechtenstein": "LIE",
    "monaco": "MCO",
    "vatican": "VAT",
    "vatican city": "VAT",
    "holy see": "VAT",
    "holy see vatican city": "VAT",
    # A few common extras that are handy in practice.
    "united states": "USA",
    "usa": "USA",
    "united states of america": "USA",
    "south korea": "KOR",
    "north korea": "PRK",
    "czech republic": "CZE",
    "czechia": "CZE",
}


def normalize_name(name: str) -> str:
    """Lower-case, strip accents and punctuation, collapse whitespace."""
    if name is None:
        return ""
    s = str(name).strip().lower()
    # Strip diacritics (Türkiye -> turkiye).
    s = "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))
    out = []
    for ch in s:
        out.append(ch if ch.isalnum() or ch.isspace() else " ")
    return " ".join("".join(out).split())


def _col(gdf_columns, candidates: list[str]) -> list[str]:
    """Return the actual column names (preserving case) matching candidates."""
    lower = {c.lower(): c for c in gdf_columns}
    return [lower[c.lower()] for c in candidates if c.lower() in lower]


# ---------------------------------------------------------------------------
# Raw loading
# ---------------------------------------------------------------------------
def _candidate_raw_paths() -> list[Path]:
    base = config.NATURAL_EARTH_DIR
    patterns = ["*.gpkg", "*.parquet", "*.shp", "*.geojson", "*.json", "*.zip"]
    found: list[Path] = []
    for pat in patterns:
        found.extend(sorted(base.glob(pat)))
    return found


def load_raw_countries(input_path: str | Path | None = None) -> gpd.GeoDataFrame:
    """Load the raw Natural Earth countries layer into a GeoDataFrame (WGS84).

    If ``input_path`` is None, auto-discover a file under
    ``data/raw/natural_earth/``.
    """
    if input_path is None:
        candidates = _candidate_raw_paths()
        if not candidates:
            raise FileNotFoundError(
                f"No raw country data found under {config.NATURAL_EARTH_DIR}. "
                "Run scripts/download_data.py first, or pass --input."
            )
        input_path = candidates[0]
        log.info("Auto-selected raw input: %s", input_path)

    input_path = Path(input_path)
    gdf = _read_any(input_path)
    if gdf.crs is None:
        log.warning("Raw layer has no CRS; assuming EPSG:4326 (WGS84 lon/lat).")
        gdf = gdf.set_crs("EPSG:4326")
    elif gdf.crs.to_epsg() != 4326:
        gdf = gdf.to_crs("EPSG:4326")
    return gdf


def _read_any(path: Path) -> gpd.GeoDataFrame:
    """Read a vector file, transparently handling zipped shapefiles."""
    suffix = path.suffix.lower()
    if suffix == ".parquet":
        return gpd.read_parquet(path)
    if suffix == ".zip":
        # Let GDAL look inside the archive for a shapefile.
        for uri in (f"zip://{path}", f"/vsizip/{path}"):
            try:
                return gpd.read_file(uri)
            except Exception as exc:  # pragma: no cover - depends on archive layout
                log.debug("read %s failed: %s", uri, exc)
        raise IOError(f"Could not read a vector layer from zip {path}")
    return gpd.read_file(path)


# ---------------------------------------------------------------------------
# Name / ISO matching
# ---------------------------------------------------------------------------
class CountryRegistry:
    """A searchable collection of :class:`CountryShape` records."""

    def __init__(self, shapes: list[CountryShape]):
        self.shapes = shapes
        self._by_iso: dict[str, CountryShape] = {}
        self._by_name: dict[str, CountryShape] = {}
        for s in shapes:
            if s.iso_a3:
                self._by_iso.setdefault(s.iso_a3.upper(), s)
            self._by_name.setdefault(normalize_name(s.name), s)

    def __len__(self) -> int:
        return len(self.shapes)

    def __iter__(self):
        return iter(self.shapes)

    def match(self, query: str) -> CountryShape | None:
        """Resolve a free-text country name or ISO code to a CountryShape."""
        if query is None:
            return None
        raw = str(query).strip()
        norm = normalize_name(raw)

        # 1. exact normalized name
        if norm in self._by_name:
            return self._by_name[norm]
        # 2. ISO code typed directly
        if raw.upper() in self._by_iso:
            return self._by_iso[raw.upper()]
        # 3. alias -> ISO
        if norm in ALIASES_TO_ISO and ALIASES_TO_ISO[norm] in self._by_iso:
            return self._by_iso[ALIASES_TO_ISO[norm]]
        # 4. forgiving substring match on normalized names
        hits = [s for n, s in self._by_name.items() if norm and (norm in n or n in norm)]
        if len(hits) == 1:
            return hits[0]
        if len(hits) > 1:
            # Prefer the closest length match to avoid e.g. "Congo" ambiguity.
            return min(hits, key=lambda s: abs(len(normalize_name(s.name)) - len(norm)))
        return None

    def resolve_chain(self, names: list[str]) -> tuple[list[CountryShape], list[str]]:
        """Resolve a list of names; returns (resolved, unresolved_names)."""
        resolved: list[CountryShape] = []
        missing: list[str] = []
        for n in names:
            shape = self.match(n)
            if shape is None:
                missing.append(n)
            else:
                resolved.append(shape)
        return resolved, missing


# ---------------------------------------------------------------------------
# Building CountryShapes
# ---------------------------------------------------------------------------
def build_country_shapes(
    gdf: gpd.GeoDataFrame,
    mode: Mode = "mainland",
    area_threshold: float = 0.01,
    proj_kind: ProjKind = "laea",
    min_area_km2: float = 0.0,
) -> list[CountryShape]:
    """Project every country into its own local km CRS and return CountryShapes."""
    name_cols = _col(gdf.columns, NAME_COLUMNS)
    iso_cols = _col(gdf.columns, ISO_COLUMNS)
    if not name_cols:
        raise ValueError(f"No recognizable name column in {list(gdf.columns)}")

    shapes: list[CountryShape] = []
    for _, row in gdf.iterrows():
        geom_lonlat: BaseGeometry = row.geometry
        if geom_lonlat is None or geom_lonlat.is_empty:
            continue
        geom_lonlat = fix_geometry(geom_lonlat)

        name = _first_str(row, name_cols) or "Unknown"
        iso = _first_iso(row, iso_cols)

        moded = apply_mode(geom_lonlat, mode, area_threshold=area_threshold)
        if moded is None or moded.is_empty:
            continue

        geom_km, center = project_to_local_km(moded, kind=proj_kind, recenter="centroid")
        geom_km = fix_geometry(geom_km)
        area_km2 = float(geom_km.area)
        if area_km2 < min_area_km2:
            continue

        shapes.append(
            CountryShape(
                name=name,
                iso_a3=iso,
                geometry=geom_km,
                area_km2=area_km2,
                bounds=tuple(float(b) for b in geom_km.bounds),  # type: ignore[arg-type]
                mode=mode,
                center_lonlat=center,
            )
        )

    # Sort by area descending: the largest country first is convenient for both
    # display and the DP longest-chain search.
    shapes.sort(key=lambda s: s.area_km2, reverse=True)
    log.info("Built %d country shapes (mode=%s).", len(shapes), mode)
    return shapes


def _first_str(row, cols: list[str]) -> str | None:
    for c in cols:
        val = row.get(c)
        if val is not None and str(val).strip() and str(val).strip().lower() != "nan":
            return str(val).strip()
    return None


def _first_iso(row, cols: list[str]) -> str:
    for c in cols:
        val = row.get(c)
        if val is None:
            continue
        s = str(val).strip().upper()
        # Natural Earth uses -99 as a sentinel for "no ISO assigned".
        if s and s not in ("-99", "NAN", "NONE", ""):
            return s
    return ""


# ---------------------------------------------------------------------------
# Persisting processed shapes
# ---------------------------------------------------------------------------
def country_shapes_to_gdf(shapes: list[CountryShape]) -> gpd.GeoDataFrame:
    """Convert CountryShapes to a GeoDataFrame of local km geometries (crs=None)."""
    records = []
    geoms = []
    for s in shapes:
        clon, clat = (s.center_lonlat or (None, None))
        records.append(
            {
                "name": s.name,
                "iso_a3": s.iso_a3,
                "area_km2": s.area_km2,
                "minx": s.bounds[0],
                "miny": s.bounds[1],
                "maxx": s.bounds[2],
                "maxy": s.bounds[3],
                "mode": s.mode,
                "center_lon": clon,
                "center_lat": clat,
            }
        )
        geoms.append(s.geometry)
    # crs=None on purpose: each geometry is in its own local km projection.
    return gpd.GeoDataFrame(pd.DataFrame.from_records(records), geometry=geoms, crs=None)


def save_country_shapes(shapes: list[CountryShape], path: str | Path) -> Path:
    """Write CountryShapes to GeoPackage (.gpkg) or GeoParquet (.parquet)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    gdf = country_shapes_to_gdf(shapes)
    suffix = path.suffix.lower()
    if suffix == ".parquet":
        gdf.to_parquet(path)
    elif suffix == ".gpkg":
        # GeoPackage needs *some* CRS; write an engineering placeholder so the
        # file is valid, but downstream code ignores it (geometries are local km).
        gdf.to_file(path, driver="GPKG", layer="countries")
    else:
        raise ValueError(f"Unsupported output extension {suffix!r}; use .gpkg or .parquet")
    log.info("Saved %d shapes -> %s", len(shapes), path)
    return path


def load_country_shapes(path: str | Path) -> CountryRegistry:
    """Load processed CountryShapes back into a CountryRegistry."""
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix == ".parquet":
        gdf = gpd.read_parquet(path)
    elif suffix == ".gpkg":
        gdf = gpd.read_file(path, layer="countries")
    else:
        raise ValueError(f"Unsupported input extension {suffix!r}; use .gpkg or .parquet")

    shapes: list[CountryShape] = []
    for _, row in gdf.iterrows():
        geom = fix_geometry(row.geometry)
        clon = row.get("center_lon")
        clat = row.get("center_lat")
        center = (float(clon), float(clat)) if clon is not None and pd.notna(clon) else None
        shapes.append(
            CountryShape(
                name=str(row["name"]),
                iso_a3=str(row.get("iso_a3") or ""),
                geometry=geom,
                area_km2=float(row["area_km2"]),
                bounds=tuple(float(row[k]) for k in ("minx", "miny", "maxx", "maxy")),  # type: ignore[arg-type]
                mode=str(row.get("mode") or "mainland"),
                center_lonlat=center,
            )
        )
    shapes.sort(key=lambda s: s.area_km2, reverse=True)
    return CountryRegistry(shapes)
