"""Local, per-country planar projections.

A single global projection badly distorts shapes far from its centre, which
would make containment tests meaningless. Instead, for every country we build
a Lambert Azimuthal Equal-Area (default) or Azimuthal Equidistant CRS centred
on that country, project into it, convert metres -> kilometres and recentre the
geometry near the origin so it can be rotated/translated freely.

Equal-area is the sensible default for a *containment / area* problem: it keeps
relative sizes faithful, which is exactly what "can A fit inside B" depends on.
Azimuthal equidistant is offered for comparison / sensitivity checks.
"""

from __future__ import annotations

from typing import Literal

from pyproj import Transformer
from shapely.affinity import translate
from shapely.geometry.base import BaseGeometry
from shapely.ops import transform as shapely_transform

WGS84 = "EPSG:4326"

ProjKind = Literal["laea", "aeqd"]


def local_crs_proj4(lon: float, lat: float, kind: ProjKind = "laea") -> str:
    """Return a PROJ string for a local projection centred on ``(lon, lat)``.

    ``laea`` -> Lambert Azimuthal Equal-Area (default, area faithful).
    ``aeqd`` -> Azimuthal Equidistant (distance faithful from the centre).
    """
    proj = "laea" if kind == "laea" else "aeqd"
    return (
        f"+proj={proj} +lat_0={lat:.8f} +lon_0={lon:.8f} "
        "+x_0=0 +y_0=0 +datum=WGS84 +units=m +no_defs"
    )


def representative_lonlat(geom_lonlat: BaseGeometry) -> tuple[float, float]:
    """Pick a stable representative centre (lon, lat) for a lon/lat geometry.

    ``representative_point`` is guaranteed to lie inside the polygon, which is
    safer than a centroid for odd shapes (e.g. crescent / multipart countries).
    """
    pt = geom_lonlat.representative_point()
    return float(pt.x), float(pt.y)


def project_to_local_km(
    geom_lonlat: BaseGeometry,
    center_lonlat: tuple[float, float] | None = None,
    kind: ProjKind = "laea",
    recenter: Literal["centroid", "bbox", "none"] = "centroid",
) -> tuple[BaseGeometry, tuple[float, float]]:
    """Project a WGS84 lon/lat geometry into a local km-scale planar geometry.

    Returns ``(geometry_km, center_lonlat)`` where ``geometry_km`` is recentred
    near the origin according to ``recenter``.
    """
    if center_lonlat is None:
        center_lonlat = representative_lonlat(geom_lonlat)
    lon, lat = center_lonlat

    transformer = Transformer.from_crs(
        WGS84, local_crs_proj4(lon, lat, kind=kind), always_xy=True
    )

    def _to_km(x, y, z=None):
        # always_xy transformer takes (x=lon, y=lat). Convert metres -> km.
        mx, my = transformer.transform(x, y)
        return (mx / 1000.0, my / 1000.0)

    geom_km = shapely_transform(_to_km, geom_lonlat)

    if recenter == "centroid":
        c = geom_km.centroid
        geom_km = translate(geom_km, xoff=-c.x, yoff=-c.y)
    elif recenter == "bbox":
        minx, miny, maxx, maxy = geom_km.bounds
        geom_km = translate(geom_km, xoff=-(minx + maxx) / 2.0, yoff=-(miny + maxy) / 2.0)
    # "none" -> leave as projected.

    return geom_km, (lon, lat)
