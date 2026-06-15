# Nested Countries Challenge Solver

Find the longest sequence of countries

```
A1 → A2 → A3 → … → An
```

such that each country `Ai+1` can be placed **fully inside** `Ai` using
**translation and rotation only**.

## The rules

- **No scaling.** Every country keeps its real-world size. You may move and
  rotate the inner country, but never resize it.
- **Strict containment.** `Ai+1` must lie *strictly* inside `Ai`; no part of its
  boundary may touch `Ai`'s boundary. This is enforced with a configurable
  inward buffer on the outer country:

  ```python
  outer.geometry.buffer(-epsilon_km).contains(inner_placed)
  ```

  The default is `epsilon_km = 1.0`.

The project first verifies a known 13-country candidate chain:

```
Russia → India → Libya → Turkey → Finland → Nepal → Togo → Qatar →
Luxembourg → Barbados → Liechtenstein → Monaco → Vatican City
```

## How it works

1. **Load & preprocess** Natural Earth Admin 0 Countries (1:10m).
2. **Project each country in its own local CRS** (Lambert Azimuthal Equal-Area
   by default), centred on the country, converted to **kilometres**, and
   recentred near the origin so it can be rotated/translated freely. A single
   global projection would badly distort shapes; a per-country projection keeps
   each country faithful.
3. **Containment oracle** (`can_contain`) decides whether one country fits in
   another via a staged search:
   - **Stage A — safe rejection:** if `inner.area ≥ outer.area`, or the inner's
     geometric *diameter* exceeds the outer's, reject. These are the *only*
     mathematically safe rejections.
   - **Stage B — coarse rotation sweep:** rotate the inner over `0…360°`.
   - **Stage C — translation candidates:** centroid-on-representative-point,
     centroid/bbox-centre on an interior grid of the inward-buffered outer.
   - **Stage D — refinement:** local angle/translation refinement to maximise
     clearance.
4. **Graph search:** build a directed acyclic graph of valid containment edges
   (edges always point from larger to strictly smaller area) and find the
   longest path via dynamic programming.
5. **Dashboard:** a single self-contained static HTML page.

### Confidence / status vocabulary

A failed search is **never** reported as mathematical impossibility:

| status | meaning |
| --- | --- |
| `proven_valid` | a concrete epsilon-respecting placement was found |
| `likely_valid` | fits the outer outline but intrudes the ε band (near-miss) |
| `rejected_area` | **safe**: inner area ≥ outer area |
| `rejected_bbox` | **safe**: inner diameter > outer diameter |
| `invalid_after_search` | full search found nothing — *search-dependent* |
| `inconclusive` | search truncated (e.g. `--max-candidates`) |

Only `rejected_area` / `rejected_bbox` are firm impossibilities.

## Install

This project uses [`uv`](https://docs.astral.sh/uv/) and an isolated `.venv`.

```bash
uv venv .venv --python 3.13
# Behind a corporate TLS proxy, add --native-tls to uv pip commands.
uv pip install --native-tls -e ".[dev]"
```

(Plain `pip` works too: `python -m venv .venv && pip install -e ".[dev]"`.)

## Download / place data

```bash
.venv/Scripts/python scripts/download_data.py
```

This fetches Natural Earth Admin 0 Countries (1:10m) into
`data/raw/natural_earth/`. If every mirror is unreachable it prints manual
instructions — download the *Admin 0 – Countries* (1:10m) layer from
[naturalearthdata.com](https://www.naturalearthdata.com/downloads/10m-cultural-vectors/)
or the [nvkelso GitHub mirror](https://github.com/nvkelso/natural-earth-vector)
and unzip it so `data/raw/natural_earth/ne_10m_admin_0_countries.shp` exists.

## Workflow

```bash
PY=.venv/Scripts/python   # on Windows; use .venv/bin/python elsewhere

# 1. get data
$PY scripts/download_data.py

# 2. preprocess (mainland mode = largest landmass only, the default)
$PY scripts/build_shapes.py --mode mainland \
    --output data/processed/countries_mainland.gpkg

# 3. verify the known 13-chain
$PY scripts/verify_chain.py \
    --countries data/processed/countries_mainland.gpkg \
    --chain "Russia,India,Libya,Turkey,Finland,Nepal,Togo,Qatar,Luxembourg,Barbados,Liechtenstein,Monaco,Vatican City" \
    --epsilon-km 1 --angle-step-deg 5 --grid-step-km 25 --refine

# 4. compute pairwise edges (coarse settings shown; this is the expensive step)
$PY scripts/compute_edges.py \
    --countries data/processed/countries_mainland.gpkg \
    --epsilon-km 1 --angle-step-deg 10 --grid-step-km 50 \
    --output outputs/edges/edges_mainland_eps1.csv

# 5. find longest chains
$PY scripts/find_chains.py \
    --edges outputs/edges/edges_mainland_eps1.csv \
    --countries data/processed/countries_mainland.gpkg \
    --min-length 8 --top-n 20

# 6. build the static interactive dashboard -> outputs/dashboard/index.html
$PY scripts/build_dashboard.py \
    --countries data/processed/countries_mainland.gpkg \
    --edges outputs/edges/edges_full.csv
```

### The dashboard

`outputs/dashboard/index.html` is a single static, dark-themed page (D3 from a
CDN; no server). It embeds the country geometries and every valid containment
edge (with its exact rotation/translation), so it is fully interactive:

* **Nesting Explorer** — build any chain; each link is validated against the
  precomputed edges and the countries are drawn *actually nested inside one
  another* by composing the stored placement transforms.
* **Top chains** gallery, an interactive **chain-graph** network, a size
  ladder, a clearance histogram, and containment leaderboards.
* A **sovereign-states-only** toggle (hides Natural Earth dependencies/territories).

Every edge shown is re-validated on the *full* (un-simplified) geometry at
build time, so featured chains genuinely respect the strict ε clearance.

## Geometry modes

| mode | behaviour |
| --- | --- |
| `mainland` (default) | keep only the single largest polygon component |
| `full` | keep the full MultiPolygon (every island) |
| `area_threshold` | keep components ≥ `--area-threshold` × largest component |

## Tests

```bash
.venv/Scripts/python -m pytest
```

Covers: mainland selection from a MultiPolygon, a square containing a smaller
rectangle, safe area rejection, the strict ε buffer rejecting near-boundary
fits, and longest-path search on a small DAG.

## Limitations & future improvements

- **Projection sensitivity.** Very large countries (Russia, Canada, USA, China,
  Brazil) are distorted by *any* planar projection. The MVP uses a local
  equal-area projection; cross-check large-country links with the
  azimuthal-equidistant projection (`build_shapes.py --proj aeqd`).
- **Natural Earth resolution.** Results depend on the 1:10m generalization.
- **Search-dependent negatives.** `invalid_after_search` / `inconclusive` are
  not proofs of impossibility — finer `--angle-step-deg` / `--grid-step-km`
  (and `--refine`) may still find a placement. Improve the candidate search
  before concluding a link is impossible.
- **Performance.** The full pairwise sweep is O(n²) pairs × search cost. Use
  `--max-countries`, `--simplify-km` and coarser steps for quick passes; the
  run is resumable (incremental CSV + settings sidecar).
