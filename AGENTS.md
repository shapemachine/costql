# costQL: agent guide

costQL prices GraphQL queries before they run: `costql build` calibrates a
live API (through a small adapter) into a static **pricing pack**;
`PricingPack.quote()` then prices any query offline, in Python or JS. Results
follow a frozen contract v1.0: every quote returns two numbers. `typical_price`
is what the query usually costs (average list sizes); `price` is the **safe max**,
the most it could cost (worst-case list sizes), guaranteed never below the real
cost, in cost-units, never dollars. The safe max is the number to bill on, and the
two are equal unless a query's size can balloon. There is no hosted service.

## Map

- `src/costql/`: the Python engine (public surface = `costql/__init__.py`
  exports + the CLI; everything else internal). `build.py` = calibrate,
  `pack.py` = load/quote, `contract.py` = frozen result shape.
- `js/`: the TypeScript quote-side port (npm `costql`). Zero runtime deps.
  Must stay conformant with Python (see below).
- `examples/adapters/`: the three reference adapters (rickmorty = minimal T1
  template). `examples/demos/`: two instrumented demo servers.
- `packs/`: five committed demo packs + `contract_examples.json`.
- `conformance/`: `queries.json` (corpus) → `quotes.json` (the frozen
  oracle both engines must reproduce).
- `docs/`: canonical documentation (the site under `site/` renders it; edit
  `docs/`, never `site/src/content/docs/docs/` which is generated).
- `skills/`: agent skills (SKILL.md format) for the three core workflows:
  writing an adapter, building a pack, interpreting quotes.

## Commands

```bash
# Python
python -m venv .venv && .venv/bin/pip install -e '.[dev]'
.venv/bin/pytest                      # offline suite; add --run-live for live tests
.venv/bin/ruff check src tests scripts
.venv/bin/costql --help               # the CLI is the source of truth for flags

# JS
cd js && npm install && npm test      # conformance vs the frozen oracle
npm run build && npm run typecheck

# Site (Astro + Starlight; docs/ + packs/ are synced in by the build)
cd site && npm install && npm run build
```

## Rules that matter

1. **Quote math changes must regenerate the oracle** in the same PR:
   `.venv/bin/python scripts/export_conformance.py`: CI diffs it. Then make
   the JS port match; the conformance suite is the referee.
2. **Never introduce dollars**: cost-units only, everywhere.
3. **The quote path stays dependency-light**: Python `costql` core imports
   numpy only (`requests` is lazy, build-time); JS has zero runtime deps.
4. **Tier honesty**: docs and code keep the framing that T1 works black-box
   on any API, while T2/T3 require the server to emit the cost-trace
   extension. A build that sees no trace downgrades to T1.
5. **Frozen surfaces**: `CONTRACT_VERSION` (result shape), `pack_version`
   (file format), and the `costql/__init__.py` exports are versioned
   promises: see CONTRIBUTING.md before touching them.
6. Docs live in `docs/` (site syncs from there). Skills reference
   `costql <cmd> --help` instead of restating flags; CI checks every
   `costql …` line in docs/skills still parses against the real CLI.
