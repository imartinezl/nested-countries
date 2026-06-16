"""Geometry validity helpers and the three geometry *modes*.

Modes decide which polygon components of a (possibly multi-part) country are
kept before projection:

* ``mainland``       - keep only the single largest polygon component (default).
* ``full``           - keep the entire MultiPolygon untouched.
* ``area_threshold`` - keep components whose area is at least a configurable
                       fraction of the largest component.
"""

from __future__ import annotations

from typing import Literal

from shapely.geometry import MultiPolygon, Polygon
from shapely.geometry.base import BaseGeometry

try:  # Shapely >= 2.0
    from shapely import make_valid as _make_valid
except Exception:  # pragma: no cover - very old shapely
    _make_valid = None

Mode = Literal["mainland", "full", "area_threshold"]
VALID_MODES = ("mainland", "full", "area_threshold")


def fix_geometry(geom: BaseGeometry) -> BaseGeometry:
    """Return a valid geometry, repairing self-intersections if needed.

    Tries ``make_valid`` first (preserves topology best), then falls back to the
    classic ``buffer(0)`` trick.
    """
    if geom is None or geom.is_empty:
        return geom
    if geom.is_valid:
        return geom
    if _make_valid is not None:
        try:
            fixed = _make_valid(geom)
            if not fixed.is_empty:
                return fixed
        except Exception:
            pass
    return geom.buffer(0)


def _polygons(geom: BaseGeometry) -> list[Polygon]:
    """Flatten any (Multi)Polygon / GeometryCollection into a list of Polygons."""
    if geom is None or geom.is_empty:
        return []
    geom_type = geom.geom_type
    if geom_type == "Polygon":
        return [geom]
    if geom_type in ("MultiPolygon", "GeometryCollection"):
        out: list[Polygon] = []
        for part in geom.geoms:
            if part.geom_type == "Polygon" and not part.is_empty:
                out.append(part)
            elif part.geom_type in ("MultiPolygon", "GeometryCollection"):
                out.extend(_polygons(part))
        return out
    return []


def largest_component(geom: BaseGeometry) -> BaseGeometry:
    """Return the single largest-area polygon component ("mainland")."""
    parts = _polygons(geom)
    if not parts:
        return geom
    return max(parts, key=lambda p: p.area)


def area_threshold_component(geom: BaseGeometry, fraction: float) -> BaseGeometry:
    """Keep polygon components whose area is >= ``fraction`` * largest area.

    ``fraction`` is relative to the *largest* component (so 0.01 keeps any
    island at least 1% of the mainland's area). Returns a Polygon or
    MultiPolygon.
    """
    parts = _polygons(geom)
    if not parts:
        return geom
    largest_area = max(p.area for p in parts)
    if largest_area <= 0:
        return geom
    kept = [p for p in parts if p.area >= fraction * largest_area]
    if not kept:
        kept = [max(parts, key=lambda p: p.area)]
    if len(kept) == 1:
        return kept[0]
    return MultiPolygon(kept)


def apply_mode(geom: BaseGeometry, mode: Mode, area_threshold: float = 0.01) -> BaseGeometry:
    """Apply a geometry mode after first repairing the input geometry."""
    if mode not in VALID_MODES:
        raise ValueError(f"Unknown mode {mode!r}; expected one of {VALID_MODES}")
    geom = fix_geometry(geom)
    if mode == "full":
        result = geom
    elif mode == "mainland":
        result = largest_component(geom)
    else:  # area_threshold
        result = area_threshold_component(geom, area_threshold)
    return fix_geometry(result)
