from shapely.geometry import box

from nested_countries.containment import can_contain
from nested_countries.models import (
    STATUS_INVALID_AFTER_SEARCH,
    STATUS_PROVEN_VALID,
    STATUS_REJECTED_AREA,
    CountryShape,
)


def cs(name: str, geom) -> CountryShape:
    return CountryShape(
        name=name, iso_a3=name[:3].upper(), geometry=geom,
        area_km2=float(geom.area), bounds=tuple(float(b) for b in geom.bounds),
        mode="full",
    )


def test_square_contains_smaller_rectangle():
    outer = cs("Outer", box(-5, -5, 5, 5))      # 10 x 10
    inner = cs("Inner", box(-1.5, -1, 1.5, 1))  # 3 x 2
    p = can_contain(outer, inner, epsilon_km=0.1, angle_step_deg=30, grid_step_km=2.0)
    assert p.valid is True
    assert p.status == STATUS_PROVEN_VALID
    assert p.clearance_km is not None and p.clearance_km > 0


def test_larger_area_is_safely_rejected():
    outer = cs("Outer", box(0, 0, 3, 3))   # area 9
    inner = cs("Inner", box(0, 0, 4, 4))   # area 16 > 9
    p = can_contain(outer, inner, epsilon_km=0.1)
    assert p.valid is False
    assert p.status == STATUS_REJECTED_AREA  # mathematically safe


def test_rotation_toggle():
    # A tall 2x10 slot; a wide 8x0.5 bar only fits if rotated ~90 degrees.
    outer = cs("Outer", box(-1, -5, 1, 5))       # 2 wide, 10 tall
    inner = cs("Inner", box(-4, -0.25, 4, 0.25))  # 8 wide, 0.5 tall

    with_rot = can_contain(outer, inner, epsilon_km=0.05, angle_step_deg=15, grid_step_km=1.0)
    assert with_rot.valid is True  # rotation lets it fit

    no_rot = can_contain(outer, inner, epsilon_km=0.05, angle_step_deg=15,
                         grid_step_km=1.0, allow_rotation=False)
    assert no_rot.valid is False           # translation-only cannot fit it
    assert no_rot.status != STATUS_REJECTED_AREA  # not a safe rejection - search-dependent


def test_fine_angle_step_one_degree_runs():
    outer = cs("Outer", box(-5, -5, 5, 5))
    inner = cs("Inner", box(-1.5, -1, 1.5, 1))
    p = can_contain(outer, inner, epsilon_km=0.1, angle_step_deg=1, grid_step_km=2.0)
    assert p.valid is True
    assert p.status == STATUS_PROVEN_VALID


def test_epsilon_buffer_rejects_near_boundary():
    outer = cs("Outer", box(-5, -5, 5, 5))         # 10 x 10, area 100
    inner = cs("Inner", box(-4.9, -4.9, 4.9, 4.9))  # 9.8 x 9.8, area ~96

    # With a tiny epsilon it fits.
    ok = can_contain(outer, inner, epsilon_km=0.01, angle_step_deg=90, grid_step_km=1.0)
    assert ok.valid is True

    # With epsilon 0.2 km the inner intrudes into the strict-containment band.
    bad = can_contain(outer, inner, epsilon_km=0.2, angle_step_deg=90, grid_step_km=1.0)
    assert bad.valid is False
    # Must NOT be a "safe" mathematical rejection - it is search/epsilon dependent.
    assert bad.status != STATUS_REJECTED_AREA
    assert bad.status in (STATUS_INVALID_AFTER_SEARCH, "likely_valid", "inconclusive")
