"""Verify that a supplied chain of countries nests, pair by pair."""

from __future__ import annotations

import argparse
from pathlib import Path

import _bootstrap  # noqa: F401

from nested_countries import config
from nested_countries.containment import can_contain
from nested_countries.data import load_country_shapes
from nested_countries.io import save_placements_csv
from nested_countries.models import SAFE_REJECTION_STATUSES, VALID_STATUSES
from nested_countries.visualize import plot_placement

log = config.setup_logging()


def main() -> int:
    ap = argparse.ArgumentParser(description="Verify a nesting chain.")
    ap.add_argument("--countries", required=True, help="Processed .gpkg/.parquet of country shapes.")
    ap.add_argument("--chain", default=",".join(config.KNOWN_CHAIN),
                    help="Comma-separated country names (default: the known 13-chain).")
    ap.add_argument("--epsilon-km", type=float, default=config.DEFAULT_EPSILON_KM)
    ap.add_argument("--angle-step-deg", type=float, default=config.DEFAULT_ANGLE_STEP_DEG)
    ap.add_argument("--grid-step-km", type=float, default=config.DEFAULT_GRID_STEP_KM)
    ap.add_argument("--refine", action=argparse.BooleanOptionalAction, default=True)
    ap.add_argument("--rotation", action=argparse.BooleanOptionalAction, default=True,
                    help="Allow rotation (default). --no-rotation = translation-only.")
    ap.add_argument("--simplify-km", type=float, default=0.0)
    ap.add_argument("--out-csv", default=str(config.PLACEMENTS_DIR / "known_chain.csv"))
    ap.add_argument("--figures-dir", default=str(config.FIGURES_DIR / "known_chain"))
    ap.add_argument("--html", action="store_true", help="Also write interactive Plotly HTML plots.")
    args = ap.parse_args()

    config.ensure_dirs()
    registry = load_country_shapes(args.countries)
    names = [n.strip() for n in args.chain.split(",") if n.strip()]
    resolved, missing = registry.resolve_chain(names)
    if missing:
        log.error("Could not resolve: %s", ", ".join(missing))
        log.error("Check spelling or add aliases in data.py.")
        return 2

    log.info("Resolved chain: %s", " -> ".join(f"{s.name}({s.iso_a3})" for s in resolved))
    figdir = Path(args.figures_dir)

    placements = []
    for outer, inner in zip(resolved, resolved[1:]):
        log.info("Testing %s -> %s ...", outer.name, inner.name)
        p = can_contain(
            outer, inner, epsilon_km=args.epsilon_km, angle_step_deg=args.angle_step_deg,
            grid_step_km=args.grid_step_km, refine=args.refine, simplify_km=args.simplify_km,
            allow_rotation=args.rotation,
        )
        placements.append(p)
        pair = f"{outer.name}__{inner.name}".replace(" ", "_")
        try:
            plot_placement(outer, inner, p, figdir / f"{pair}.png", also_html=args.html)
        except Exception as exc:
            log.warning("Plot failed for %s: %s", pair, exc)

    _print_table(placements)
    save_placements_csv(placements, args.out_csv)
    log.info("Placements CSV -> %s", args.out_csv)
    log.info("Figures -> %s", figdir)

    overall = _overall_status(placements)
    log.info("OVERALL CHAIN STATUS: %s", overall)
    return 0


def _print_table(placements) -> None:
    header = f"{'OUTER':<16}{'INNER':<16}{'VALID':<7}{'CONF':<8}{'ANGLE':>7}{'CLEAR_KM':>10}  STATUS"
    print("\n" + header)
    print("-" * len(header))
    for p in placements:
        ang = f"{p.angle_deg:.1f}" if p.angle_deg is not None else "-"
        clr = f"{p.clearance_km:.2f}" if p.clearance_km is not None else "-"
        print(f"{(p.outer_name or '')[:15]:<16}{(p.inner_name or '')[:15]:<16}"
              f"{('yes' if p.valid else 'no'):<7}{p.confidence:<8}{ang:>7}{clr:>10}  {p.status}")
    print()


def _overall_status(placements) -> str:
    if all(p.status in VALID_STATUSES and p.valid for p in placements):
        return "VALID (every link has a concrete placement)"
    if any(p.status in SAFE_REJECTION_STATUSES for p in placements):
        bad = [f"{p.outer_name}->{p.inner_name}" for p in placements if p.status in SAFE_REJECTION_STATUSES]
        return f"INVALID (mathematically impossible link: {', '.join(bad)})"
    return "INCONCLUSIVE (one or more links are search-dependent; try finer steps)"


if __name__ == "__main__":
    raise SystemExit(main())
