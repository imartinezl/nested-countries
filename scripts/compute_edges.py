"""Compute pairwise containment edges over all (ordered) country pairs.

Writes incrementally to CSV with a settings sidecar, so the run is resumable:
re-running skips pairs already present unless --force is given.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import _bootstrap  # noqa: F401
from tqdm import tqdm

from nested_countries import config
from nested_countries.containment import build_outer_context, can_contain
from nested_countries.data import load_country_shapes
from nested_countries.io import EdgeWriter, load_existing_pairs, write_settings_sidecar
from nested_countries.models import SearchSettings

log = config.setup_logging()


def main() -> int:
    ap = argparse.ArgumentParser(description="Compute pairwise containment edges.")
    ap.add_argument("--countries", required=True)
    ap.add_argument("--epsilon-km", type=float, default=config.DEFAULT_EPSILON_KM)
    ap.add_argument("--angle-step-deg", type=float, default=10.0)
    ap.add_argument("--grid-step-km", type=float, default=50.0)
    ap.add_argument("--refine", action=argparse.BooleanOptionalAction, default=True)
    ap.add_argument("--rotation", action=argparse.BooleanOptionalAction, default=True,
                    help="Allow rotation (default). --no-rotation = translation-only containment.")
    ap.add_argument("--simplify-km", type=float, default=0.0)
    ap.add_argument("--max-candidates", type=int, default=None,
                    help="Cap placement tests per pair (truncated -> inconclusive).")
    ap.add_argument("--max-countries", type=int, default=None,
                    help="Use only the N largest countries (handy for quick runs).")
    ap.add_argument("--only-smaller-area", action=argparse.BooleanOptionalAction, default=True,
                    help="Only test inner with strictly smaller area than outer (default on).")
    ap.add_argument("--force", action="store_true", help="Ignore cache; recompute everything.")
    ap.add_argument("--output", default=str(config.EDGES_DIR / "edges_mainland_eps1.csv"))
    args = ap.parse_args()

    config.ensure_dirs()
    registry = load_country_shapes(args.countries)
    shapes = list(registry)
    if args.max_countries:
        shapes = shapes[: args.max_countries]
    log.info("Computing edges over %d countries.", len(shapes))

    settings = SearchSettings(
        epsilon_km=args.epsilon_km, angle_step_deg=args.angle_step_deg,
        grid_step_km=args.grid_step_km, refine=args.refine, allow_rotation=args.rotation,
        mode=shapes[0].mode if shapes else "mainland",
        max_candidates=args.max_candidates, extra={"simplify_km": args.simplify_km,
                                                   "only_smaller_area": args.only_smaller_area},
    )
    write_settings_sidecar(args.output, settings)

    existing = set() if args.force else load_existing_pairs(args.output)
    if existing:
        log.info("Resuming: %d pairs already computed.", len(existing))

    # Group inners by outer so the expensive per-outer context (buffer + grid)
    # is built once and reused across every inner.
    jobs: list[tuple[object, list]] = []
    n_pairs = 0
    for outer in shapes:
        inners = []
        for inner in shapes:
            if inner is outer:
                continue
            if args.only_smaller_area and inner.area_km2 >= outer.area_km2:
                continue
            if (outer.iso_a3, inner.iso_a3) in existing:
                continue
            inners.append(inner)
        if inners:
            jobs.append((outer, inners))
            n_pairs += len(inners)

    log.info("%d pairs to evaluate across %d outer countries.", n_pairs, len(jobs))
    n_valid = 0
    with EdgeWriter(args.output, resume=not args.force) as writer:
        with tqdm(total=n_pairs, desc="edges", unit="pair") as bar:
            for outer, inners in jobs:
                ctx = build_outer_context(
                    outer, epsilon_km=args.epsilon_km,
                    grid_step_km=args.grid_step_km, simplify_km=args.simplify_km,
                )
                for inner in inners:
                    p = can_contain(
                        outer, inner, angle_step_deg=args.angle_step_deg,
                        refine=args.refine, max_candidates=args.max_candidates,
                        allow_rotation=args.rotation, ctx=ctx,
                    )
                    writer.write(p)
                    if p.valid:
                        n_valid += 1
                    bar.update(1)

    log.info("Done. %d valid edges written -> %s", n_valid, args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
