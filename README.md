# costQL

[![CI](https://github.com/shapemachine/costql/actions/workflows/ci.yml/badge.svg)](https://github.com/shapemachine/costql/actions/workflows/ci.yml)

**Price GraphQL queries before you run them.** costQL calibrates a live GraphQL
API into a **pricing pack**, one self-contained JSON file, then quotes any
query against that pack fully offline. No server, no network, no measurement.

```bash
pip install costql        # Python: build packs + quote
npm install costql        # JS/TS: quote packs (build stays in Python)
```

## 60 seconds to a quote (offline)

```python
from costql import PricingPack

pack = PricingPack.load("packs/tmdb_t3.json")   # a committed demo pack
quote = pack.quote('{ movie(id:"27205"){ cast(limit:8){ person{ name } } } }')

quote["price"]        # safe billable ceiling, in cost-units (never dollars)
quote["typical_price"]# fair average estimate
quote["confidence"]   # high | medium | low: cyclic queries are flagged, not billed
```

Or from the command line:

```bash
costql quote --pack packs/tmdb_t3.json '{ movie(id:"27205"){ title } }'
```

Every result follows a **frozen output contract (v1.0)**: `price` is always
present, always a number, always safe to bill on. See
[docs/contract.md](docs/contract.md).

## How it works

1. **Build (seller side, once per schema):** run `costql probe <url>` to see
   what your endpoint supports, write a ~90-line adapter that tells costQL
   where your API is and how to fill in its arguments (or hand that job to a
   coding agent: see [docs/agents.md](docs/agents.md)), then run
   `costql build`. costQL introspects the schema, measures a set of clean
   calibration queries, and fits a per-resolver cost model.
2. **Ship the pack:** the output is one static file (schema + fitted costs +
   any observed outside hosts). Vendor it into any app.
3. **Quote (app side, forever):** load the pack, price queries locally; in
   Python or JavaScript.

## One currency, three fidelities

costQL prices in **work-ms**, the summed duration of the real work a query
causes. Three *fidelities* of one engine, set by how much your API's
instrumentation exposes:

| Tier | Needs | Sees | Blur it removes |
|------|-------|------|-----------------|
| **T1** | nothing: any GraphQL endpoint you can query | whole-request wall-clock | none; always available |
| **T2** | server emits per-resolver timings | each resolver's own work-ms | parallelism no longer hides work |
| **T3** | server also emits loader keys/cache status | sharing observed exactly; batch curves learned; paid hosts named | nothing hidden |

**Honesty first:** T1 works black-box against *any* GraphQL API: the
[Rick & Morty case study](docs/results/rickmorty.md) hit ~4% mean error with a
93-line adapter and zero server changes. T2/T3 require your server to emit
costQL's cost-trace extension; the demo packs are mostly T3 because we
instrumented the demo servers. **Your first pack will be T1: that is the
designed starting point**, and it already gives you a safe billable ceiling.

## Measured accuracy

On held-out queries against real backends (methodology in
[docs/evaluation.md](docs/evaluation.md): calibration and evaluation sets are
disjoint; there is no query→price lookup):

- **TMDB demo (instrumented):** mean error T1 17% / T2 11% / T3 11%
- **Rick & Morty (public API, not ours):** ~4% mean error at T1, ceiling never
  under the real cost
- **Northwind (batch-heavy SQLite):** heavy entity sharing is where T3 pays:
  T2 315% vs T3 12% on hub queries ([case study](docs/results/northwind.md))

Cyclic-recursion queries (whose runtime dedup is unknowable pre-execution) are
automatically flagged **low confidence** and priced as a structural ceiling.
Flagged, not silently billed.

## What costQL is not

- **Not a service.** The pack is static and local by design: no sidecar, no
  pricing endpoint, no extra API call.
- **Not billing.** costQL speaks cost-units, never dollars; converting to money
  is the consuming app's job.
- **Not a rate limiter.** It prices; what you do with the price is up to you.

## Docs

Quickstart, the adapter guide, tier fidelity, the output contract, evaluation
methodology, and an interactive playground: **https://costql.com**

## License

Apache-2.0
