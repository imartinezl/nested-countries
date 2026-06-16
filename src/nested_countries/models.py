"""Structured records used across the Nested Countries pipeline.

These are deliberately small, typed dataclasses so they can be serialized to
CSV/JSON and passed between the data, containment and graph-search modules
without dragging heavy GeoDataFrame state along.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from shapely.geometry.base import BaseGeometry

# ---------------------------------------------------------------------------
# Containment status vocabulary.
#
# IMPORTANT: only ``rejected_area`` (and the equally-safe bounding metrics that
# are mathematically conservative) may be treated as "truly impossible". Every
# other failure is *search dependent* and must never be reported as a proof of
# impossibility.
# ---------------------------------------------------------------------------
STATUS_PROVEN_VALID = "proven_valid"          # a concrete placement was found
STATUS_LIKELY_VALID = "likely_valid"          # found, but near the epsilon margin
STATUS_INCONCLUSIVE = "inconclusive"          # search exhausted, no firm answer
STATUS_REJECTED_AREA = "rejected_area"        # SAFE: inner area >= outer area
STATUS_REJECTED_BBOX = "rejected_bbox"        # SAFE: conservative diameter bound
STATUS_INVALID_AFTER_SEARCH = "invalid_after_search"  # searched, none worked

VALID_STATUSES = {STATUS_PROVEN_VALID, STATUS_LIKELY_VALID}
# Statuses that are mathematically safe rejections (never a search artefact).
SAFE_REJECTION_STATUSES = {STATUS_REJECTED_AREA, STATUS_REJECTED_BBOX}

CONFIDENCE_HIGH = "high"
CONFIDENCE_MEDIUM = "medium"
CONFIDENCE_LOW = "low"
CONFIDENCE_NONE = "none"


@dataclass
class CountryShape:
    """A country geometry already projected to a local, planar km-scale CRS.

    The geometry is centred near the origin so it can be rotated and translated
    freely. ``area_km2`` and ``bounds`` are cached because they are queried a
    lot during the pairwise edge sweep.
    """

    name: str
    iso_a3: str
    geometry: BaseGeometry
    area_km2: float
    bounds: tuple[float, float, float, float]
    mode: str
    # Longitude / latitude of the representative centre used to build the local
    # projection. Useful for debugging projection sensitivity.
    center_lonlat: tuple[float, float] | None = None

    @property
    def width_km(self) -> float:
        minx, _, maxx, _ = self.bounds
        return maxx - minx

    @property
    def height_km(self) -> float:
        _, miny, _, maxy = self.bounds
        return maxy - miny

    @property
    def diameter_km(self) -> float:
        """Diameter of the bounding box (a cheap upper bound on extent)."""
        return (self.width_km ** 2 + self.height_km ** 2) ** 0.5


@dataclass
class Placement:
    """Result of asking whether ``inner`` fits inside ``outer``.

    ``valid`` only means a concrete, epsilon-respecting placement was found.
    A ``valid == False`` result is *not* a proof of impossibility unless
    ``status`` is one of :data:`SAFE_REJECTION_STATUSES`.
    """

    outer_iso: str
    inner_iso: str
    valid: bool
    angle_deg: Optional[float]
    dx: Optional[float]
    dy: Optional[float]
    clearance_km: Optional[float]
    method: str
    confidence: str
    status: str = STATUS_INCONCLUSIVE
    reason: Optional[str] = None
    # Names are handy when the placement is serialized on its own.
    outer_name: Optional[str] = None
    inner_name: Optional[str] = None

    def as_row(self) -> dict:
        """Flatten to a CSV/JSON friendly dict."""
        return {
            "outer_iso": self.outer_iso,
            "inner_iso": self.inner_iso,
            "outer_name": self.outer_name,
            "inner_name": self.inner_name,
            "valid": self.valid,
            "status": self.status,
            "confidence": self.confidence,
            "angle_deg": self.angle_deg,
            "dx": self.dx,
            "dy": self.dy,
            "clearance_km": self.clearance_km,
            "method": self.method,
            "reason": self.reason,
        }


@dataclass
class SearchSettings:
    """Settings recorded alongside outputs so runs are reproducible."""

    epsilon_km: float = 1.0
    angle_step_deg: float = 5.0
    grid_step_km: float = 25.0
    refine: bool = True
    allow_rotation: bool = True
    mode: str = "mainland"
    area_threshold: float = 0.0
    dataset: str = "Natural Earth Admin 0 Countries (1:10m)"
    max_candidates: int | None = None
    extra: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "epsilon_km": self.epsilon_km,
            "angle_step_deg": self.angle_step_deg,
            "grid_step_km": self.grid_step_km,
            "refine": self.refine,
            "allow_rotation": self.allow_rotation,
            "mode": self.mode,
            "area_threshold": self.area_threshold,
            "dataset": self.dataset,
            "max_candidates": self.max_candidates,
            **self.extra,
        }
