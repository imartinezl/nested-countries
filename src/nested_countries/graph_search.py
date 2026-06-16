"""Build a containment DAG and find the longest nesting chains.

Every valid edge goes from a larger-area country to a strictly smaller-area one
(guaranteed by the ``rejected_area`` rule), so the graph is acyclic. The longest
nesting chain is therefore the longest path in a DAG, solved with a linear-time
dynamic program over nodes in descending-area (topological) order.
"""

from __future__ import annotations

import logging
from collections import Counter
from dataclasses import dataclass

import networkx as nx
import pandas as pd

from .data import CountryRegistry
from .models import VALID_STATUSES

log = logging.getLogger("nested_countries.graph_search")


@dataclass
class ChainResult:
    nodes: list[str]            # node ids (iso or name) outer -> inner
    names: list[str]            # human-readable names
    length: int
    min_clearance_km: float | None
    areas_km2: list[float] | None = None  # area of each country in the chain


def node_ids(registry: CountryRegistry) -> dict[str, str]:
    """Map each country's (unique) NAME to a collision-safe node id.

    Natural Earth assigns many dependencies the ISO code of their *sovereign*
    (e.g. Baikonur -> KAZ, Clipperton I. -> FRA, Coral Sea Is. -> AUS). Keying
    nodes by ISO would therefore merge a territory into its parent country. So
    we use the ISO only when it is unique across the dataset, and fall back to
    the (unique) name otherwise.
    """
    iso_counts = Counter(s.iso_a3 for s in registry if s.iso_a3)
    return {
        s.name: (s.iso_a3 if (s.iso_a3 and iso_counts[s.iso_a3] == 1) else s.name)
        for s in registry
    }


def _coerce(v) -> str:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return ""
    return str(v)


def build_graph(
    edges: pd.DataFrame,
    registry: CountryRegistry,
    statuses: set[str] | None = None,
    min_confidence: str | None = None,
) -> nx.DiGraph:
    """Build a directed containment graph from an edges table.

    An edge ``outer -> inner`` is included when its ``status`` is in ``statuses``
    (default: the genuinely valid ones) and its confidence is at least
    ``min_confidence`` if provided.
    """
    statuses = statuses or set(VALID_STATUSES)
    conf_rank = {"none": 0, "low": 1, "medium": 2, "high": 3}
    min_rank = conf_rank.get(min_confidence or "none", 0)

    nid = node_ids(registry)
    by_name = {s.name: s for s in registry}
    iso_counts = Counter(s.iso_a3 for s in registry if s.iso_a3)
    by_iso = {s.iso_a3: s for s in registry if s.iso_a3 and iso_counts[s.iso_a3] == 1}

    def resolve(iso: str, name: str) -> str | None:
        # The name is unique per Natural Earth feature, so prefer it.
        if name and name in by_name:
            return nid[name]
        if iso and iso in by_iso:
            return nid[by_iso[iso].name]
        return None

    g = nx.DiGraph()
    for s in registry:
        g.add_node(nid[s.name], name=s.name, iso=s.iso_a3, area_km2=s.area_km2)

    n_edges = 0
    for _, row in edges.iterrows():
        status = str(row.get("status", ""))
        if status not in statuses:
            continue
        if conf_rank.get(str(row.get("confidence", "none")), 0) < min_rank:
            continue
        u = resolve(_coerce(row.get("outer_iso")), _coerce(row.get("outer_name")))
        v = resolve(_coerce(row.get("inner_iso")), _coerce(row.get("inner_name")))
        if u is None or v is None or u == v:
            continue
        # Safety: keep the DAG strictly area-decreasing.
        if g.nodes[u]["area_km2"] <= g.nodes[v]["area_km2"]:
            continue
        clearance = row.get("clearance_km")
        g.add_edge(u, v, clearance_km=float(clearance) if pd.notna(clearance) else None)
        n_edges += 1

    log.info("Built containment graph: %d nodes, %d edges.", g.number_of_nodes(), n_edges)
    return g


def _dp_longest(g: nx.DiGraph) -> tuple[dict, dict]:
    """Return (best_len, next_node) where best_len[n] is the longest chain
    (in node count) starting at n."""
    # Topological order: by area descending => edges always go forward.
    order = sorted(g.nodes, key=lambda n: g.nodes[n]["area_km2"], reverse=True)
    best_len: dict[str, int] = {}
    nxt: dict[str, str | None] = {}
    for n in reversed(order):  # smallest area first
        best = 1
        choice = None
        for succ in g.successors(n):
            cand = 1 + best_len.get(succ, 1)
            if cand > best:
                best, choice = cand, succ
        best_len[n] = best
        nxt[n] = choice
    return best_len, nxt


def _reconstruct(start: str, nxt: dict) -> list[str]:
    chain = [start]
    cur = start
    seen = {start}
    while nxt.get(cur) is not None:
        cur = nxt[cur]
        if cur in seen:  # cycle guard (shouldn't happen in a DAG)
            break
        chain.append(cur)
        seen.add(cur)
    return chain


def _to_result(g: nx.DiGraph, nodes: list[str]) -> ChainResult:
    names = [g.nodes[n].get("name", n) for n in nodes]
    clearances = [
        g.edges[nodes[i], nodes[i + 1]].get("clearance_km")
        for i in range(len(nodes) - 1)
        if g.has_edge(nodes[i], nodes[i + 1])
    ]
    clearances = [c for c in clearances if c is not None]
    areas = [float(g.nodes[n].get("area_km2", 0.0)) for n in nodes]
    return ChainResult(
        nodes=nodes,
        names=names,
        length=len(nodes),
        min_clearance_km=min(clearances) if clearances else None,
        areas_km2=areas,
    )


def longest_chain(g: nx.DiGraph) -> ChainResult:
    """Return the single longest nesting chain."""
    if g.number_of_nodes() == 0:
        return ChainResult([], [], 0, None)
    best_len, nxt = _dp_longest(g)
    start = max(best_len, key=lambda n: best_len[n])
    return _to_result(g, _reconstruct(start, nxt))


def top_chains(g: nx.DiGraph, top_n: int = 10, min_length: int = 1) -> list[ChainResult]:
    """Return up to ``top_n`` distinct long chains, longest first.

    For each possible start node we reconstruct the longest chain starting there,
    then de-duplicate and keep the longest ``top_n``.
    """
    if g.number_of_nodes() == 0:
        return []
    best_len, nxt = _dp_longest(g)
    starts = sorted(best_len, key=lambda n: best_len[n], reverse=True)
    seen: set[tuple[str, ...]] = set()
    results: list[ChainResult] = []
    for s in starts:
        chain = _reconstruct(s, nxt)
        key = tuple(chain)
        if key in seen or len(chain) < min_length:
            continue
        seen.add(key)
        results.append(_to_result(g, chain))
        if len(results) >= top_n:
            break
    return results


def diversity_metrics(chains: list[ChainResult]) -> dict:
    """Simple diversity stats across a set of chains."""
    if not chains:
        return {"n_chains": 0, "unique_countries": 0, "max_length": 0, "mean_length": 0.0}
    countries: set[str] = set()
    for c in chains:
        countries.update(c.nodes)
    lengths = [c.length for c in chains]
    return {
        "n_chains": len(chains),
        "unique_countries": len(countries),
        "max_length": max(lengths),
        "mean_length": round(sum(lengths) / len(lengths), 2),
    }
