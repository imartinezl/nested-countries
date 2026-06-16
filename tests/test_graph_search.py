import pandas as pd
from shapely.geometry import box

from nested_countries.data import CountryRegistry
from nested_countries.graph_search import build_graph, longest_chain, top_chains
from nested_countries.models import STATUS_PROVEN_VALID, CountryShape


def cs(name, iso, area):
    # A unit box scaled so .area_km2 matches `area`; geometry is irrelevant here.
    g = box(0, 0, area ** 0.5, area ** 0.5)
    return CountryShape(name=name, iso_a3=iso, geometry=g, area_km2=float(area),
                        bounds=tuple(g.bounds), mode="full")


def _registry():
    return CountryRegistry([
        cs("Aland", "AAA", 100),
        cs("Bland", "BBB", 50),
        cs("Cland", "CCC", 40),
        cs("Dland", "DDD", 10),
    ])


def _edges():
    rows = []
    for u, v in [("AAA", "BBB"), ("BBB", "DDD"), ("AAA", "CCC"), ("CCC", "DDD")]:
        rows.append({
            "outer_iso": u, "inner_iso": v, "outer_name": u, "inner_name": v,
            "valid": True, "status": STATUS_PROVEN_VALID, "confidence": "high",
            "clearance_km": 5.0,
        })
    return pd.DataFrame(rows)


def test_longest_path_on_small_dag():
    g = build_graph(_edges(), _registry())
    best = longest_chain(g)
    assert best.length == 3              # A -> B -> D  (or A -> C -> D)
    assert best.nodes[0] == "AAA"
    assert best.nodes[-1] == "DDD"


def test_top_chains_are_distinct_and_sorted():
    g = build_graph(_edges(), _registry())
    chains = top_chains(g, top_n=5, min_length=2)
    assert len(chains) >= 2
    # sorted by length descending
    assert chains[0].length >= chains[-1].length
    # distinct
    keys = {tuple(c.nodes) for c in chains}
    assert len(keys) == len(chains)


def test_empty_graph_is_safe():
    g = build_graph(pd.DataFrame(columns=["outer_iso", "inner_iso", "valid", "status", "confidence"]),
                    _registry())
    best = longest_chain(g)
    # No edges -> longest "chain" is a single country.
    assert best.length == 1
