# costQL

[![CI](https://github.com/shapemachine/costql/actions/workflows/ci.yml/badge.svg)](https://github.com/shapemachine/costql/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/costql?logo=pypi&logoColor=white&label=PyPI)](https://pypi.org/project/costql/)
[![npm](https://img.shields.io/npm/v/costql?logo=npm&label=npm)](https://www.npmjs.com/package/costql)

**Measure what a GraphQL query costs before it runs.** Every other GraphQL
cost tool makes you hand-author a number on each field, a guess that rots as
the resolver changes. costQL times your real API once into a **pricing pack**
(one self-contained JSON file), then prices any query fully offline: no server,
no network. One number you can **bill on or block on**, guaranteed never below
the real cost.

```bash
pip install costql        # Python: build packs + quote
npm install costql        # JS/TS: quote packs (build stays in Python)
```

## 60 seconds to a quote (offline)

```python
from costql import PricingPack

pack = PricingPack.demo("tmdb_t3")   # a demo pack bundled in the package
quote = pack.quote('{ movie(id:"27205"){ cast(limit:8){ person{ name } } } }')

quote["price"]        # safe billable ceiling, in cost-units (never dollars)
quote["typical_price"]# fair average estimate
quote["confidence"]   # high | medium | low: cyclic queries are flagged, not billed
```

Or from the command line:

```bash
costql quote --demo tmdb_t3 '{ movie(id:"27205"){ title } }'
```

(Quoting your own API? Build a pack with `costql build` and pass it with
`--pack your_pack.json`. The bundled `--demo` pack is just for the tour.)

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
- **Northwind (batch-heavy SQLite):** heavy entity sharing, the API shape that
  needs the sharing-watching tier: 12% mean error at T3 on hub queries that a
  sharing-blind fidelity cannot price ([case study](docs/results/northwind.md))

Cyclic-recursion queries (whose runtime dedup is unknowable pre-execution) are
automatically flagged **low confidence** and priced as a structural ceiling.
Flagged, not silently billed.

## Adding limits on expensive queries?

If you came to stop expensive or abusive queries, a query-complexity limit,
demand control, DoS protection, you're in the right neighborhood. All of those
need the same thing: **a cost per query to threshold on.** Today you hand-author
that cost (`@cost` directives, per-field weights) and hope the numbers are right.

costQL is the measured version of that number. It won't reject the query for
you, you still write `if quote["price"] > budget: reject`, but now the budget
means something, because the price came from timing your real API, not a guess.
And the queries hand-weights get wrong (batched resolvers priced far too high,
recursive queries priced too low) are exactly the ones costQL gets right. See
[docs/guides/query-limits.md](docs/guides/query-limits.md).

## Why not just write this yourself?

You could count fields and multiply by pagination in an afternoon, most people
do, and there are libraries for it (`graphql-query-complexity`,
`graphql-cost-analysis`, Apollo demand control). They share one weakness: **you**
supply each field's cost by hand. That guess is wrong in the two cases that
matter most:

- **Batching.** A resolver behind a DataLoader does a list of 100 in one
  round-trip, not 100. Field-multiplier math prices it at 100× and punishes your
  best-built resolver. costQL prices shared work once; on our share-heaviest test
  that cut error from 315% to 12%.
- **Recursion.** A cyclic query is the classic GraphQL DoS. A hand-authored
  weight never sees it coming. costQL flags cycles automatically and prices them
  at a safe ceiling.

The hard part isn't summing a tree, it's knowing the per-field number is *right*,
and the only way to know is to measure the real API. That's the part a
from-scratch build skips, and it's why the DIY version quietly undercharges
(billing) or lets the expensive query through (protection). If your API is
simple and you never bill on the number, hand-weights are fine. The moment
undercharging or a DoS query actually costs you, you want it measured.

## What costQL is not

- **Not a service.** The pack is static and local by design: no sidecar, no
  pricing endpoint, no extra API call.
- **Not billing.** costQL speaks cost-units, never dollars; converting to money
  is the consuming app's job.
- **Not a rate limiter.** It prices; you decide the limit and enforce it, see
  [limiting expensive queries](docs/guides/query-limits.md).

## Docs

Quickstart, the adapter guide, tier fidelity, the output contract, evaluation
methodology, and an interactive playground: **https://costql.com**

## License

Apache-2.0
