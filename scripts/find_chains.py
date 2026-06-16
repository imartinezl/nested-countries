"""Read an edge CSV and find the longest nesting chains."""

from __future__ import annotations

import argparse
from pathlib import Path

import _bootstrap  # noqa: F401

from nested_countries import config
from nested_countries.data import load_country_shapes
from nested_countries.graph_search import (
    build_graph,
    diversity_metrics,
    longest_chain,
    top_chains,
)
from nested_countries.io import load_edges, save_chains
from nested_countries.models import STATUS_LIKELY_VALID, STATUS_PROVEN_VALID

log = config.setup_logging()


def main() -> int:
    ap = argparse.ArgumentParser(description="Find longest nesting chains from edges.")
    ap.add_argument("--edges", required=True)
    ap.add_argument("--countries", required=True)
    ap.add_argument("--min-length", type=int, default=1)
    ap.add_argument("--top-n", type=int, default=20)
    ap.add_argument("--min-confidence", choices=["none", "low", "medium", "high"], default="none")
    ap.add_argument("--include-likely", action="store_true",
                    help="Also treat likely_valid (near-epsilon) edges as valid.")
    ap.add_argument("--output", default=str(config.CHAINS_DIR / "chains.json"))
    args = ap.parse_args()

    config.ensure_dirs()
    registry = load_country_shapes(args.countries)
    edges = load_edges(args.edges)

    statuses = {STATUS_PROVEN_VALID}
    if args.include_likely:
        statuses.add(STATUS_LIKELY_VALID)

    g = build_graph(edges, registry, statuses=statuses, min_confidence=args.min_confidence)

    best = longest_chain(g)
    log.info("Longest chain length: %d", best.length)
    log.info("Longest chain: %s", " -> ".join(best.names))

    chains = top_chains(g, top_n=args.top_n, min_length=args.min_length)
    log.info("Diversity: %s", diversity_metrics(chains))

    save_chains(chains, args.output)
    # Also drop a CSV alongside for convenience.
    save_chains(chains, str(Path(args.output).with_suffix(".csv")))

    print("\nTOP CHAINS")
    print("-" * 60)
    for i, c in enumerate(chains[: min(args.top_n, len(chains))], 1):
        print(f"{i:>2}. (len {c.length}) {' -> '.join(c.names)}")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
