# Contributing to costQL

## Dev setup

```bash
git clone https://github.com/shapemachine/costql && cd costql
python -m venv .venv && .venv/bin/pip install -e '.[dev]'
.venv/bin/pytest                 # offline suite: no servers, no keys
.venv/bin/ruff check src tests scripts

cd js && npm install
npm test                         # TS conformance suite vs the frozen oracle
npm run build && npm run typecheck
```

Live tests (opt-in, hit real endpoints): `pytest --run-live`. The Rick & Morty
build test needs only internet; the TMDB test needs the demo server up with
keys (see `examples/demos/tmdb/README.md`).

## The compatibility contracts

Three things are versioned promises; treat changes to them as API changes:

1. **The Python public surface**: exactly what `costql/__init__.py` exports,
   plus the CLI subcommands/flags. Everything else is internal.
2. **The output contract** (`CONTRACT_VERSION`, currently 1.0): the shape of
   every quote result. Additive field → 1.1; removal/rename → 2.0 **and** a
   major package release. `costql.contract.validate` is the enforcement;
   `packs/contract_examples.json` is the executable spec.
3. **`pack_version`** (currently 1): the pack file format. Independent of the
   package version; each release's notes state the range it reads. Packs must
   stay forward-readable within a major.

The npm package is conformance-pinned, not lockstep-versioned: `costql@0.1.x`
(npm) must reproduce the Python engine's `conformance/quotes.json` oracle
exactly (within the file's tolerance policy). CI enforces this on every PR.

## Changing quote math

Any intentional change that shifts prices must regenerate the oracle:

```bash
.venv/bin/python scripts/export_conformance.py
```

The `conformance/quotes.json` diff **is** the review artifact: reviewers judge
the price changes there. CI fails if the committed oracle doesn't match the
engine. The TS port must then be updated in the same PR to stay conformant.

## Versioning & releases

Semver. Releases are tag-driven (`git tag v0.x.y && git push --tags`):
TestPyPI → manual gate → PyPI → npm, via `.github/workflows/release.yml`
(trusted publishing, no stored tokens). Update `CHANGELOG.md` and
`src/costql/_version.py` + `js/package.json` together in the release PR.

## Style

- Python: `ruff` (config in `pyproject.toml`); match the existing compact style.
- Cost-units only: never dollars, anywhere, including docs and examples.
- Honesty framing: anything that touches tier documentation keeps the "T1 works
  everywhere; T2/T3 need seller-side instrumentation" statement intact.
