from shapely.geometry import MultiPolygon, box

from nested_countries.geometry_modes import (
    apply_mode,
    area_threshold_component,
    fix_geometry,
    largest_component,
)


def _multi():
    big = box(0, 0, 10, 10)        # area 100
    medium = box(20, 0, 25, 5)     # area 25
    tiny = box(30, 0, 31, 1)       # area 1
    return MultiPolygon([big, medium, tiny])


def test_mainland_picks_largest_component():
    geom = largest_component(_multi())
    assert geom.geom_type == "Polygon"
    assert abs(geom.area - 100) < 1e-6


def test_area_threshold_keeps_above_fraction():
    # threshold 0.1 of largest (100) -> keep components with area >= 10: big + medium.
    geom = area_threshold_component(_multi(), 0.1)
    assert geom.geom_type == "MultiPolygon"
    assert abs(geom.area - 125) < 1e-6


def test_full_mode_keeps_everything():
    geom = apply_mode(_multi(), "full")
    assert abs(geom.area - 126) < 1e-6


def test_fix_geometry_repairs_bowtie():
    from shapely.geometry import Polygon

    bowtie = Polygon([(0, 0), (2, 2), (2, 0), (0, 2)])  # self-intersecting
    assert not bowtie.is_valid
    fixed = fix_geometry(bowtie)
    assert fixed.is_valid
