"""The containment oracle: can ``inner`` be placed inside ``outer``?

Only translation and rotation are allowed (no scaling). Strict containment is
enforced via an inward buffer on the outer country::

    safe_outer = outer.geometry.buffer(-epsilon_km)
    safe_outer.contains(inner_placed)

Search is staged:

  A. fast, *mathematically safe* rejection (area, diameter);
  B. coarse rotation sweep with several translation-candidate strategies;
  C. grid of interior translation anchors;
  D. local refinement of the best candidate.

Design rule (very important): a failed search is NEVER reported as a proof of
impossibility. Only :data:`SAFE_REJECTION_STATUSES` (area / diameter) are firm
"impossible"; everything else is ``invalid_after_search`` / ``inconclusive`` and
clearly labelled as search-dependent.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from shapely.affinity import rotate, translate
from shapely.geometry import Point
from shapely.geometry.base import BaseGeometry
from shapely.prepared import prep

from .geometry_modes import fix_geometry
from .models import (
    CONFIDENCE_HIGH,
    CONFIDENCE_LOW,
    CONFIDENCE_MEDIUM,
    CONFIDENCE_NONE,
    STATUS_INCONCLUSIVE,
    STATUS_INVALID_AFTER_SEARCH,
    STATUS_LIKELY_VALID,
    STATUS_PROVEN_VALID,
    STATUS_REJECTED_AREA,
    STATUS_REJECTED_BBOX,
    CountryShape,
    Placement,
)


# ---------------------------------------------------------------------------
# Safe geometric bounds
# ---------------------------------------------------------------------------
def geometric_diameter(geom: BaseGeometry) -> float:
    """Maximum distance between any two points of ``geom`` (its diameter).

    Rotation/translation invariant, so it gives a SAFE necessary condition:
    if ``diameter(inner) > diameter(outer)`` the inner can never fit.
    Computed on the convex hull for speed.
    """
    hull = geom.convex_hull
    coords = _coords(hull)
    if len(coords) < 2:
        return 0.0
    pts = np.asarray(coords)
    # Pairwise distances on hull vertices (hull is small) -> exact diameter.
    diff = pts[:, None, :] - pts[None, :, :]
    d2 = (diff ** 2).sum(axis=-1)
    return float(math.sqrt(d2.max()))


def _coords(geom: BaseGeometry) -> list[tuple[float, float]]:
    gt = geom.geom_type
    if gt == "Polygon":
        return list(geom.exterior.coords)
    if gt in ("LineString", "LinearRing"):
        return list(geom.coords)
    if gt == "Point":
        return [(geom.x, geom.y)]
    if gt in ("MultiPolygon", "GeometryCollection", "MultiLineString"):
        out: list[tuple[float, float]] = []
        for g in geom.geoms:
            out.extend(_coords(g))
        return out
    return []


def _anchor(geom: BaseGeometry, kind: str) -> tuple[float, float]:
    if kind == "centroid":
        c = geom.centroid
        return (c.x, c.y)
    minx, miny, maxx, maxy = geom.bounds
    return ((minx + maxx) / 2.0, (miny + maxy) / 2.0)


def effective_grid_step(safe_outer: BaseGeometry, step_km: float, min_cells: int = 24) -> float:
    """Adapt the grid step to the outer's size.

    A fixed absolute step (e.g. 50 km) is far too coarse for a small country -
    a ~25 km-wide nation would get almost no interior candidate points. We cap
    the step so the bounding box is covered by at least ``min_cells`` cells on
    its longer side, while never going finer than is sensible.
    """
    minx, miny, maxx, maxy = safe_outer.bounds
    extent = max(maxx - minx, maxy - miny)
    if extent <= 0:
        return step_km
    eff = min(step_km, extent / min_cells)
    return max(eff, 0.02)  # floor (km) to avoid a point explosion for tiny shapes


def _grid_points(safe_outer: BaseGeometry, step_km: float) -> list[tuple[float, float]]:
    """Interior anchor points spaced ``step_km`` apart inside ``safe_outer``."""
    minx, miny, maxx, maxy = safe_outer.bounds
    if not np.isfinite([minx, miny, maxx, maxy]).all():
        return []
    prepared = prep(safe_outer)
    xs = np.arange(minx, maxx + step_km, step_km)
    ys = np.arange(miny, maxy + step_km, step_km)
    pts: list[tuple[float, float]] = []
    for x in xs:
        for y in ys:
            if prepared.contains(Point(x, y)):
                pts.append((float(x), float(y)))
    return pts


# ---------------------------------------------------------------------------
# A single placement test
# ---------------------------------------------------------------------------
def _try_place(
    rotated_inner: BaseGeometry,
    anchor_xy: tuple[float, float],
    target_xy: tuple[float, float],
    prepared_safe,
    safe_bounds: tuple[float, float, float, float],
) -> BaseGeometry | None:
    """Translate ``rotated_inner`` so ``anchor`` -> ``target``; return it if it
    lies strictly inside the (prepared) safe outer, else None."""
    dx = target_xy[0] - anchor_xy[0]
    dy = target_xy[1] - anchor_xy[1]
    placed = translate(rotated_inner, xoff=dx, yoff=dy)
    # Cheap bbox reject before the expensive contains test.
    pminx, pminy, pmaxx, pmaxy = placed.bounds
    if pminx < safe_bounds[0] or pminy < safe_bounds[1] or pmaxx > safe_bounds[2] or pmaxy > safe_bounds[3]:
        return None
    if prepared_safe.contains(placed):
        return placed
    return None


# ---------------------------------------------------------------------------
# Per-outer context (cached across many inners)
# ---------------------------------------------------------------------------
@dataclass
class OuterContext:
    """Everything about the *outer* country that does not depend on the inner.

    Building this is the expensive part of a containment test (buffering the
    polygon, preparing it, and rasterizing the interior grid). When one outer is
    tested against many inners - as in the full pairwise sweep - this should be
    built once and reused, which is dramatically faster.
    """

    outer: CountryShape
    geom: BaseGeometry
    d_outer: float
    safe_outer: BaseGeometry | None
    prepared_safe: object | None
    safe_bounds: tuple[float, float, float, float] | None
    eff_step: float
    grid: list[tuple[float, float]]
    targets: list[tuple[float, float]]
    epsilon_km: float
    simplify_km: float


def build_outer_context(
    outer: CountryShape,
    epsilon_km: float = 1.0,
    grid_step_km: float = 25.0,
    simplify_km: float = 0.0,
) -> OuterContext:
    """Precompute the inner-independent outer geometry, buffer and grid."""
    geom = fix_geometry(outer.geometry)
    d_outer = geometric_diameter(geom)
    if simplify_km and simplify_km > 0:
        geom = fix_geometry(geom.simplify(min(simplify_km, d_outer / 150.0)))

    rep = (geom.representative_point().x, geom.representative_point().y)
    centroid = (geom.centroid.x, geom.centroid.y)

    safe = geom.buffer(-epsilon_km)
    if safe.is_empty:
        return OuterContext(outer, geom, d_outer, None, None, None,
                            grid_step_km, [], [rep, centroid], epsilon_km, simplify_km)
    safe = fix_geometry(safe)
    eff_step = effective_grid_step(safe, grid_step_km)
    grid = _grid_points(safe, eff_step)
    return OuterContext(
        outer, geom, d_outer, safe, prep(safe), safe.bounds,
        eff_step, grid, [rep, centroid] + grid, epsilon_km, simplify_km,
    )


# ---------------------------------------------------------------------------
# Main oracle
# ---------------------------------------------------------------------------
def can_contain(
    outer: CountryShape,
    inner: CountryShape,
    epsilon_km: float = 1.0,
    angle_step_deg: float = 5.0,
    grid_step_km: float = 25.0,
    refine: bool = True,
    max_candidates: int | None = None,
    simplify_km: float = 0.0,
    allow_rotation: bool = True,
    ctx: "OuterContext | None" = None,
) -> Placement:
    """Decide whether ``inner`` fits strictly inside ``outer``.

    Returns a :class:`Placement`. ``valid`` is True only when a concrete
    epsilon-respecting placement is found.

    If ``allow_rotation`` is False the inner is only translated (orientation
    fixed at 0°) - a strictly harder "translation-only" containment test.
    """
    base = dict(
        outer_iso=outer.iso_a3,
        inner_iso=inner.iso_a3,
        outer_name=outer.name,
        inner_name=inner.name,
    )

    # ---- Stage A: SAFE fast rejection ------------------------------------
    if inner.area_km2 >= outer.area_km2:
        return Placement(
            **base, valid=False, angle_deg=None, dx=None, dy=None, clearance_km=None,
            method="area", confidence=CONFIDENCE_NONE, status=STATUS_REJECTED_AREA,
            reason=(
                f"inner area {inner.area_km2:,.0f} km^2 >= outer area "
                f"{outer.area_km2:,.0f} km^2 (mathematically impossible)"
            ),
        )

    # Reuse a prebuilt outer context if given (huge speedup in the pairwise
    # sweep); otherwise build it for this single test.
    if ctx is None:
        ctx = build_outer_context(outer, epsilon_km, grid_step_km, simplify_km)
    else:
        epsilon_km = ctx.epsilon_km
        simplify_km = ctx.simplify_km
    outer_geom = ctx.geom
    d_outer = ctx.d_outer

    inner_geom = fix_geometry(inner.geometry)
    # Diameter is computed on the TRUE geometry so the safe rejection is never
    # corrupted by simplification.
    d_inner = geometric_diameter(inner_geom)
    if simplify_km and simplify_km > 0:
        inner_geom = fix_geometry(inner_geom.simplify(min(simplify_km, d_inner / 150.0)))

    # SAFE diameter rejection: an inner whose diameter exceeds the outer's can
    # never fit, regardless of rotation/translation.
    if d_inner > d_outer + 1e-9:
        return Placement(
            **base, valid=False, angle_deg=None, dx=None, dy=None, clearance_km=None,
            method="diameter", confidence=CONFIDENCE_NONE, status=STATUS_REJECTED_BBOX,
            reason=(
                f"inner diameter {d_inner:,.1f} km > outer diameter "
                f"{d_outer:,.1f} km (mathematically impossible)"
            ),
        )

    if ctx.safe_outer is None:
        return Placement(
            **base, valid=False, angle_deg=None, dx=None, dy=None, clearance_km=None,
            method="buffer", confidence=CONFIDENCE_NONE, status=STATUS_INVALID_AFTER_SEARCH,
            reason=(
                f"outer vanishes under the {epsilon_km} km inward buffer "
                "(search-dependent on epsilon, not a proof of impossibility)"
            ),
        )
    safe_outer = ctx.safe_outer
    prepared_safe = ctx.prepared_safe
    safe_bounds = ctx.safe_bounds

    # ---- Stage B/C: coarse rotation sweep + translation candidates -------
    eff_step = ctx.eff_step
    targets = ctx.targets
    angle_list = [0.0] if not allow_rotation else _angle_order(angle_step_deg)

    # Rotate lazily and cache: a capped / early-exiting search then never pays
    # to rotate angles it does not actually reach (important for fine steps).
    rot_cache: dict[float, tuple] = {}

    def get_rot(angle: float):
        r = rot_cache.get(angle)
        if r is None:
            rg = rotate(inner_geom, angle, origin="centroid")
            r = (rg, _anchor(rg, "centroid"), _anchor(rg, "bbox"))
            rot_cache[angle] = r
        return r

    # Candidate ordering matters a lot when max_candidates is small: try the
    # primary positions (representative point, centroid) across ALL angles first
    # so rotation-dependent fits near the centre are found quickly, then expand
    # to the full interior grid. ``_angle_order`` spreads the angles around the
    # circle so even a truncated fine sweep samples all orientations.
    primary_targets = targets[:2]
    grid_targets = targets[2:]

    def _candidates():
        for tg in primary_targets:
            for angle in angle_list:
                rg, ca, ba = get_rot(angle)
                yield angle, rg, ca, tg
                yield angle, rg, ba, tg
        for tg in grid_targets:
            for angle in angle_list:
                rg, ca, ba = get_rot(angle)
                yield angle, rg, ca, tg
                yield angle, rg, ba, tg

    tested = 0
    truncated = False
    best = None  # (clearance, angle, placed)
    for angle, r, anchor_xy, target in _candidates():
        if max_candidates is not None and tested >= max_candidates:
            truncated = True
            break
        tested += 1
        placed = _try_place(r, anchor_xy, target, prepared_safe, safe_bounds)
        if placed is not None:
            cle = outer_geom.boundary.distance(placed.boundary)
            best = (cle, angle, placed)
            break  # early exit: a witness exists; refine it below

    # ---- Stage D: refinement --------------------------------------------
    if best is not None and refine:
        best = _refine(inner_geom, outer_geom, prepared_safe, safe_bounds, best,
                       angle_step_deg, eff_step, allow_rotation=allow_rotation)

    if best is not None:
        clearance, angle, placed = best
        c = placed.centroid
        confidence = CONFIDENCE_HIGH if clearance >= epsilon_km + 0.5 else CONFIDENCE_MEDIUM
        if simplify_km and simplify_km > 0:
            confidence = min(confidence, CONFIDENCE_MEDIUM, key=_conf_rank)
        return Placement(
            **base, valid=True, angle_deg=angle, dx=float(c.x), dy=float(c.y),
            clearance_km=float(clearance), method="rotate+translate+refine",
            confidence=confidence, status=STATUS_PROVEN_VALID,
            reason=f"placement found; clearance {clearance:.2f} km beyond outer boundary",
        )

    # ---- No safe placement. Did it at least fit ignoring epsilon? --------
    near = _search_raw_fit(inner_geom, outer_geom, angle_list, targets, get_rot,
                           max_tests=max_candidates)
    if near is not None:
        angle, placed, clearance = near
        c = placed.centroid
        return Placement(
            **base, valid=False, angle_deg=float(angle), dx=float(c.x), dy=float(c.y),
            clearance_km=float(clearance), method="rotate+translate",
            confidence=CONFIDENCE_LOW, status=STATUS_LIKELY_VALID,
            reason=(
                "fits inside the outer outline but intrudes into the "
                f"{epsilon_km} km strict-containment band (search-dependent)"
            ),
        )

    status = STATUS_INCONCLUSIVE if truncated else STATUS_INVALID_AFTER_SEARCH
    note = "search truncated by max_candidates" if truncated else "exhausted coarse+refine search"
    return Placement(
        **base, valid=False, angle_deg=None, dx=None, dy=None, clearance_km=None,
        method="rotate+translate", confidence=CONFIDENCE_NONE, status=status,
        reason=(
            f"no containing placement found ({note}); this is SEARCH-DEPENDENT, "
            "not a proof of impossibility - try finer angle_step/grid_step"
        ),
    )


_CONF_ORDER = {CONFIDENCE_NONE: 0, CONFIDENCE_LOW: 1, CONFIDENCE_MEDIUM: 2, CONFIDENCE_HIGH: 3}


def _conf_rank(c: str) -> int:
    return _CONF_ORDER.get(c, 0)


def _angle_order(step_deg: float) -> list[float]:
    """Angles 0..360 by ``step_deg`` in a spread (bit-reversal-like) order.

    Visiting 0, 180, 90, 270, ... before filling in between means a *truncated*
    fine sweep still samples the whole circle, instead of getting stuck near 0.
    """
    angles = [float(a) for a in np.arange(0.0, 360.0, float(step_deg))]
    n = len(angles)
    if n <= 1:
        return angles
    seen = [False] * n
    order: list[float] = []
    stride = n
    while stride >= 1:
        for i in range(0, n, stride):
            if not seen[i]:
                seen[i] = True
                order.append(angles[i])
        if stride == 1:
            break
        stride = max(1, stride // 2)
    for i in range(n):  # safety: include any missed index
        if not seen[i]:
            order.append(angles[i])
    return order


def _refine(
    inner_geom: BaseGeometry,
    outer_geom: BaseGeometry,
    prepared_safe,
    safe_bounds,
    best,
    angle_step_deg: float,
    grid_step_km: float,
    allow_rotation: bool = True,
):
    """Locally improve clearance around the current best placement."""
    clearance, angle, placed = best
    # When rotation is disabled, refine translation only.
    angle_deltas = (-1.0, 0.0, 1.0) if allow_rotation else (0.0,)

    # Two zoom levels for both angle and translation.
    for a_step, t_step in (
        (angle_step_deg / 5.0, grid_step_km / 5.0),
        (angle_step_deg / 25.0, grid_step_km / 25.0),
    ):
        improved = True
        while improved:
            improved = False
            cx, cy = placed.centroid.x, placed.centroid.y
            for dmul in angle_deltas:
                da = dmul * a_step
                rotated = rotate(inner_geom, angle + da, origin="centroid")
                anchor = _anchor(rotated, "centroid")
                for dxo in (-t_step, 0.0, t_step):
                    for dyo in (-t_step, 0.0, t_step):
                        if da == 0.0 and dxo == 0.0 and dyo == 0.0:
                            continue
                        target = (cx + dxo, cy + dyo)
                        cand = _try_place(rotated, anchor, target, prepared_safe, safe_bounds)
                        if cand is None:
                            continue
                        cle = outer_geom.boundary.distance(cand.boundary)
                        if cle > clearance + 1e-9:
                            clearance, angle, placed = cle, angle + da, cand
                            improved = True
    return (clearance, angle, placed)


def _search_raw_fit(inner_geom, outer_geom, angle_list, targets, get_rot=None,
                    max_tests: int | None = None):
    """Look for a placement that fits the *unbuffered* outer (epsilon ignored).

    This is only a "near-miss" detector, so it must stay cheap: it is capped at
    ``max_tests`` placement checks. Without a cap a fine angle sweep (e.g. 1°)
    would make this dominate the whole run.
    """
    prepared_raw = prep(outer_geom)
    raw_bounds = outer_geom.bounds
    tested = 0
    for angle in angle_list:
        if get_rot is not None:
            rotated, ca, ba = get_rot(angle)
            anchors = (ca, ba)
        else:
            rotated = rotate(inner_geom, float(angle), origin="centroid")
            anchors = (_anchor(rotated, "centroid"), _anchor(rotated, "bbox"))
        for anchor in anchors:
            for target in targets:
                if max_tests is not None and tested >= max_tests:
                    return None
                tested += 1
                placed = _try_place(rotated, anchor, target, prepared_raw, raw_bounds)
                if placed is not None:
                    clearance = outer_geom.boundary.distance(placed.boundary)
                    return (float(angle), placed, float(clearance))
    return None
