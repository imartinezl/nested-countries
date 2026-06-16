"""Build the static, interactive HTML dashboard.

The page is a single static file (no backend). All the data needed for
interactivity is embedded as JSON:

* ``countries`` - id -> {name, area, sovereign, rings} where ``rings`` are the
  display-simplified polygon coordinates in the *local km, centroid-centred*
  frame (the same frame the placement transforms live in).
* ``adj`` - outer_id -> {inner_id: [angle_deg, dx, dy, clearance_km]} for every
  proven-valid containment edge. These are the rigid (rotate+translate)
  placements, so the browser can reconstruct exactly how one country sits
  inside another and compose them to nest a whole chain.
* ``chainsAll`` / ``chainsSov`` - precomputed longest chains (all Admin-0
  entries vs sovereign states only).

D3 (loaded from a CDN) powers the charts and network graph; the nesting
visualization is hand-rolled SVG.
"""

from __future__ import annotations

import base64
import json
import logging
from pathlib import Path

import networkx as nx
import pandas as pd
from jinja2 import Environment, FileSystemLoader, select_autoescape
from shapely.affinity import rotate, translate
from shapely.prepared import prep

from . import config
from .data import CountryRegistry
from .geometry_modes import fix_geometry
from .graph_search import node_ids, top_chains
from .io import load_edges
from .models import STATUS_PROVEN_VALID

log = logging.getLogger("nested_countries.dashboard")

# Natural Earth TYPE values we treat as sovereign states.
SOVEREIGN_TYPES = {"sovereign country", "country"}

# Nested-rings logo/favicon (inline SVG), reused for the page icon.
LOGO_SVG = (
    "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 64 64'>"
    "<rect width='64' height='64' rx='13' fill='#080c18'/>"
    "<rect x='9' y='9' width='46' height='46' rx='10' fill='none' stroke='#39e0c4' stroke-width='3'/>"
    "<rect x='19' y='19' width='26' height='26' rx='6' fill='none' stroke='#7aa2ff' stroke-width='3'/>"
    "<rect x='27.5' y='27.5' width='9' height='9' rx='2.5' fill='#b388ff'/>"
    "</svg>"
)


def _favicon_data_uri() -> str:
    b64 = base64.b64encode(LOGO_SVG.encode("utf-8")).decode("ascii")
    return f"data:image/svg+xml;base64,{b64}"


# ---------------------------------------------------------------------------
# Geometry export
# ---------------------------------------------------------------------------
def _rings(geom, tol: float) -> list[list[list[float]]]:
    """Return display-simplified exterior rings as [[ [x,y], ... ], ...]."""
    g = geom.simplify(tol, preserve_topology=True)
    if g.is_empty:
        g = geom
    out: list[list[list[float]]] = []
    polys = []
    if g.geom_type == "Polygon":
        polys = [g]
    elif g.geom_type == "MultiPolygon":
        polys = list(g.geoms)
    for poly in polys:
        ring = [[round(float(x), 1), round(float(y), 1)] for x, y in poly.exterior.coords]
        if len(ring) >= 4:
            out.append(ring)
    return out


def export_countries(registry: CountryRegistry, sovereign: dict[str, bool],
                     idmap: dict[str, str]) -> dict:
    countries = {}
    for s in registry:
        cid = idmap[s.name]
        # Display-only simplification (crisp): cap at ~3 km but never coarser
        # than diameter/500, so big countries get fine ~3 km outlines while tiny
        # states keep near-full detail. Does not affect any computed result.
        tol = max(min(3.0, s.diameter_km / 500.0), 0.02)
        countries[cid] = {
            "name": s.name,
            "iso": s.iso_a3,
            "area": round(s.area_km2, 1),
            "sov": bool(sovereign.get(cid, True)),
            "rings": _rings(s.geometry, tol),
        }
    return countries


# ---------------------------------------------------------------------------
# Sovereignty classification (from raw Natural Earth TYPE)
# ---------------------------------------------------------------------------
def sovereignty_map(registry: CountryRegistry, idmap: dict[str, str]) -> tuple[dict[str, bool], bool]:
    """Classify each country as sovereign vs territory using Natural Earth TYPE.

    Returns ``(map, available)`` keyed by node id. If the raw layer can't be
    read, every country defaults to sovereign and ``available`` is False (the UI
    hides the toggle).
    """
    try:
        from .data import NAME_COLUMNS, _col, _first_str, load_raw_countries

        gdf = load_raw_countries()
    except Exception as exc:  # pragma: no cover - depends on raw data presence
        log.warning("Sovereignty unavailable (%s); treating all as sovereign.", exc)
        return {}, False

    name_cols = _col(gdf.columns, NAME_COLUMNS)
    type_col = next((c for c in gdf.columns if c.lower() == "type"), None)
    # Classify by (unique) Natural Earth NAME so dependencies are not merged.
    raw: dict[str, bool] = {}
    for _, row in gdf.iterrows():
        name = _first_str(row, name_cols) or "Unknown"
        t = str(row.get(type_col, "")).strip().lower() if type_col else "sovereign country"
        raw[name] = t in SOVEREIGN_TYPES

    out = {idmap[s.name]: raw.get(s.name, True) for s in registry}
    return out, (type_col is not None)


# ---------------------------------------------------------------------------
# Edges -> adjacency with placement transforms
# ---------------------------------------------------------------------------
def export_adjacency(edges: pd.DataFrame, registry: CountryRegistry,
                     idmap: dict[str, str]) -> tuple[dict, dict]:
    """Return (adjacency, status_counts).

    adjacency[outer_id][inner_id] = [angle_deg, dx, dy, clearance_km].
    """
    by_name = {s.name: s for s in registry}

    def to_id(name, iso):
        name = "" if (name is None or (isinstance(name, float) and pd.isna(name))) else str(name)
        if name and name in by_name:
            return idmap[name]
        return None

    adj: dict[str, dict[str, list]] = {}
    status_counts: dict[str, int] = {}
    for r in edges.itertuples(index=False):
        status = str(getattr(r, "status", ""))
        status_counts[status] = status_counts.get(status, 0) + 1
        if status != STATUS_PROVEN_VALID:
            continue
        oid = to_id(getattr(r, "outer_name", None), getattr(r, "outer_iso", None))
        iid = to_id(getattr(r, "inner_name", None), getattr(r, "inner_iso", None))
        if oid is None or iid is None or oid == iid:
            continue
        try:
            entry = [round(float(r.angle_deg), 1), round(float(r.dx), 1),
                     round(float(r.dy), 1), round(float(r.clearance_km), 2)]
        except (TypeError, ValueError):
            continue
        adj.setdefault(oid, {})[iid] = entry
    return adj, status_counts


# ---------------------------------------------------------------------------
# Chains
# ---------------------------------------------------------------------------
def revalidate_adjacency(adj: dict, registry: CountryRegistry, idmap: dict[str, str],
                         epsilon_km: float) -> tuple[dict, int]:
    """Replay every stored placement on the FULL geometry and keep only the
    edges that genuinely clear ``epsilon_km``.

    The edges were searched on simplified geometry for speed, so a placement can
    intrude a little on the true boundary. This pass guarantees that every edge
    in the dashboard (and therefore every nesting drawn / validated) is real,
    and replaces the clearance with its exact full-geometry value.
    """
    shape_by_id = {idmap[s.name]: s for s in registry}
    out: dict[str, dict[str, list]] = {}
    dropped = 0
    for oid, inners in adj.items():
        outer = shape_by_id.get(oid)
        if outer is None:
            continue
        safe = fix_geometry(outer.geometry.buffer(-epsilon_km))
        if safe.is_empty:
            dropped += len(inners)
            continue
        prepared = prep(safe)
        boundary = outer.geometry.boundary
        kept: dict[str, list] = {}
        for iid, (ang, dx, dy, _clr) in inners.items():
            inner = shape_by_id.get(iid)
            if inner is None:
                continue
            r = rotate(inner.geometry, ang, origin="centroid")
            c = r.centroid
            placed = translate(r, xoff=dx - c.x, yoff=dy - c.y)
            if prepared.contains(placed):
                kept[iid] = [ang, dx, dy, round(boundary.distance(placed.boundary), 2)]
            else:
                dropped += 1
        if kept:
            out[oid] = kept
    return out, dropped


def chain_graph(adj: dict, registry: CountryRegistry, idmap: dict[str, str]) -> nx.DiGraph:
    """Build a DiGraph straight from the (validated) adjacency."""
    g = nx.DiGraph()
    for s in registry:
        g.add_node(idmap[s.name], name=s.name, iso=s.iso_a3, area_km2=s.area_km2)
    for oid, inners in adj.items():
        for iid, entry in inners.items():
            if g.has_node(oid) and g.has_node(iid) and \
               g.nodes[oid]["area_km2"] > g.nodes[iid]["area_km2"]:
                g.add_edge(oid, iid, clearance_km=entry[3])
    return g


def _chain_payload(chains) -> list[dict]:
    return [
        {"ids": c.nodes, "names": c.names, "length": c.length,
         "minClear": c.min_clearance_km}
        for c in chains
    ]


def compute_chains(g: nx.DiGraph, sovereign: dict[str, bool], top_n: int = 12):
    """Return (chainsAll, chainsSov) longest-first from a validated graph."""
    chains_all = top_chains(g, top_n=top_n, min_length=2)
    sov_nodes = [n for n in g.nodes if sovereign.get(n, True)]
    chains_sov = top_chains(g.subgraph(sov_nodes), top_n=top_n, min_length=2)
    return _chain_payload(chains_all), _chain_payload(chains_sov)


# ---------------------------------------------------------------------------
# Main entry
# ---------------------------------------------------------------------------
def build_dashboard(
    registry: CountryRegistry,
    edges_csv: Path | str,
    output_html: Path | None = None,
    settings: dict | None = None,
    top_n: int = 12,
) -> Path:
    config.ensure_dirs()
    output_html = Path(output_html or (config.DASHBOARD_DIR / "index.html"))
    edges = load_edges(edges_csv)
    idmap = node_ids(registry)

    log.info("Classifying sovereignty ...")
    sovereign, sov_available = sovereignty_map(registry, idmap)

    log.info("Exporting geometries for %d countries ...", len(registry))
    countries = export_countries(registry, sovereign, idmap)

    eps = float((settings or {}).get("epsilon_km", config.DEFAULT_EPSILON_KM))
    log.info("Building adjacency from edges ...")
    adj, status_counts = export_adjacency(edges, registry, idmap)
    raw_edges = sum(len(v) for v in adj.values())

    log.info("Re-validating %d placements on full geometry (eps=%.1f km) ...", raw_edges, eps)
    adj, dropped = revalidate_adjacency(adj, registry, idmap, eps)
    n_edges = sum(len(v) for v in adj.values())
    log.info("Kept %d / %d edges (dropped %d that intruded the boundary).",
             n_edges, raw_edges, dropped)

    log.info("Computing longest chains ...")
    g = chain_graph(adj, registry, idmap)
    chains_all, chains_sov = compute_chains(g, sovereign, top_n=top_n)

    data = {
        "countries": countries,
        "adj": adj,
        "chainsAll": chains_all,
        "chainsSov": chains_sov,
        "sovAvailable": sov_available,
        "meta": {
            "nCountries": len(countries),
            "nEdges": n_edges,
            "nPairs": int(len(edges)),
            "statusCounts": status_counts,
            "longestAll": chains_all[0]["length"] if chains_all else 0,
            "longestSov": chains_sov[0]["length"] if chains_sov else 0,
            "dataset": (settings or {}).get("dataset", config.DATASET_NAME),
            "mode": (settings or {}).get("mode", config.DEFAULT_MODE),
            "epsilon_km": (settings or {}).get("epsilon_km", config.DEFAULT_EPSILON_KM),
            "angleStep": (settings or {}).get("angle_step_deg", config.DEFAULT_ANGLE_STEP_DEG),
            "gridStep": (settings or {}).get("grid_step_km", config.DEFAULT_GRID_STEP_KM),
            "simplifyKm": (settings or {}).get("simplify_km", 0),
            "maxCandidates": (settings or {}).get("max_candidates"),
            "rotation": (settings or {}).get("allow_rotation", True),
        },
        "settings": settings or {},
    }

    env = Environment(
        loader=FileSystemLoader(str(config.TEMPLATES_DIR)),
        autoescape=select_autoescape(["html"]),
    )
    template = env.get_template("dashboard.html.j2")
    html = template.render(
        meta=data["meta"],
        favicon=_favicon_data_uri(),
        data_json=json.dumps(data, separators=(",", ":"), ensure_ascii=False),
    )
    output_html.write_text(html, encoding="utf-8")
    log.info("Dashboard written -> %s (%.1f KB, %d edges embedded)",
             output_html, len(html) / 1024, n_edges)
    return output_html
