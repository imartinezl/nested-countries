"""Plotting helpers for verified placements and chains.

Static PNG/SVG via matplotlib (Agg backend, no display needed) and optional
interactive HTML via Plotly. All plots draw the outer boundary and the placed
inner boundary so a reader can eyeball the containment and clearance.
"""

from __future__ import annotations

import logging
from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # headless
import matplotlib.pyplot as plt  # noqa: E402
from shapely.affinity import rotate, translate  # noqa: E402
from shapely.geometry.base import BaseGeometry  # noqa: E402

from .models import CountryShape, Placement  # noqa: E402

log = logging.getLogger("nested_countries.visualize")


def reconstruct_placed(inner: CountryShape, placement: Placement) -> BaseGeometry | None:
    """Rebuild the placed inner geometry from a Placement record."""
    if placement.angle_deg is None or placement.dx is None or placement.dy is None:
        return None
    rotated = rotate(inner.geometry, placement.angle_deg, origin="centroid")
    c = rotated.centroid
    return translate(rotated, xoff=placement.dx - c.x, yoff=placement.dy - c.y)


def _ring_xy(geom: BaseGeometry):
    """Yield (xs, ys) for every exterior ring of a (Multi)Polygon."""
    gt = geom.geom_type
    if gt == "Polygon":
        x, y = geom.exterior.xy
        yield list(x), list(y)
    elif gt in ("MultiPolygon", "GeometryCollection"):
        for g in geom.geoms:
            if g.geom_type == "Polygon":
                x, y = g.exterior.xy
                yield list(x), list(y)


def plot_placement(
    outer: CountryShape,
    inner: CountryShape,
    placement: Placement,
    out_path: str | Path,
    also_html: bool = False,
) -> Path:
    """Save a PNG (and optionally an interactive HTML) of one placement."""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    placed = reconstruct_placed(inner, placement)

    fig, ax = plt.subplots(figsize=(7, 7))
    for xs, ys in _ring_xy(outer.geometry):
        ax.plot(xs, ys, color="#1f4e79", lw=1.6, label="_outer")
        ax.fill(xs, ys, color="#1f4e79", alpha=0.06)

    if placed is not None:
        color = "#2e8b57" if placement.valid else "#c0392b"
        for xs, ys in _ring_xy(placed):
            ax.plot(xs, ys, color=color, lw=1.6, label="_inner")
            ax.fill(xs, ys, color=color, alpha=0.18)

    ax.set_aspect("equal", adjustable="datalim")
    ax.set_xlabel("km")
    ax.set_ylabel("km")
    angle = f"{placement.angle_deg:.1f} deg" if placement.angle_deg is not None else "n/a"
    clr = f"{placement.clearance_km:.2f} km" if placement.clearance_km is not None else "n/a"
    ax.set_title(
        f"{outer.name} -> {inner.name}\n"
        f"valid={placement.valid} | status={placement.status} | "
        f"confidence={placement.confidence}\n"
        f"angle={angle} | clearance={clr}",
        fontsize=10,
    )
    ax.grid(True, ls=":", alpha=0.4)
    fig.tight_layout()
    fig.savefig(out_path, dpi=130)
    plt.close(fig)

    if also_html:
        try:
            _plot_placement_html(outer, inner, placed, placement, out_path.with_suffix(".html"))
        except Exception as exc:  # pragma: no cover - plotly is optional at runtime
            log.warning("Plotly HTML for %s failed: %s", out_path.name, exc)
    return out_path


def _plot_placement_html(outer, inner, placed, placement, html_path: Path) -> None:
    import plotly.graph_objects as go

    fig = go.Figure()
    for xs, ys in _ring_xy(outer.geometry):
        fig.add_trace(go.Scatter(x=xs, y=ys, mode="lines", name=f"outer: {outer.name}",
                                 line=dict(color="#1f4e79", width=2), fill="toself",
                                 fillcolor="rgba(31,78,121,0.06)"))
    if placed is not None:
        color = "#2e8b57" if placement.valid else "#c0392b"
        for xs, ys in _ring_xy(placed):
            fig.add_trace(go.Scatter(x=xs, y=ys, mode="lines", name=f"inner: {inner.name}",
                                     line=dict(color=color, width=2), fill="toself",
                                     fillcolor="rgba(46,139,87,0.18)"))
    fig.update_layout(
        title=f"{outer.name} → {inner.name} (valid={placement.valid}, {placement.status})",
        xaxis_title="km", yaxis_title="km",
        yaxis=dict(scaleanchor="x", scaleratio=1),
        template="plotly_white", width=720, height=720,
    )
    fig.write_html(html_path, include_plotlyjs="cdn", full_html=True)


def plot_chain_areas(names: list[str], areas_km2: list[float], out_path: str | Path) -> Path:
    """Bar chart of country areas along a chain (largest -> smallest)."""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig, ax = plt.subplots(figsize=(9, 4.5))
    ax.bar(range(len(names)), areas_km2, color="#1f4e79")
    ax.set_yscale("log")
    ax.set_xticks(range(len(names)))
    ax.set_xticklabels(names, rotation=45, ha="right", fontsize=8)
    ax.set_ylabel("area (km², log scale)")
    ax.set_title("Country areas along the chain")
    fig.tight_layout()
    fig.savefig(out_path, dpi=130)
    plt.close(fig)
    return out_path
