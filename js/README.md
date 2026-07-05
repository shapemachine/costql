# costql (JavaScript/TypeScript)

**Quote GraphQL query costs offline** from a [costQL](https://costql.com)
pricing pack. This package is the *quote side only*: packs are built once with
the Python `costql` package (`pip install 'costql[build]'`), then consumed
anywhere: Node, browsers, edge runtimes. Zero runtime dependencies.

```bash
npm install costql
```

```ts
import { PricingPack } from "costql";

// Node
const pack = await PricingPack.load("packs/tmdb_t3.json");
// Browser
const pack = PricingPack.fromObject(await (await fetch("/packs/tmdb_t3.json")).json());

const quote = pack.quote('{ movie(id:"27205"){ cast(limit:8){ person{ name } } } }');
quote.price          // safe billable ceiling, in cost-units (never dollars)
quote.typical_price  // fair average estimate
quote.confidence     // "high" | "medium" | "low": cyclic queries are flagged
quote.breakdown      // per-resolver cost lines (T2/T3 packs)
```

Every result follows costQL's **frozen output contract v1.0**: the same shape
the Python engine emits. `validate(result)` returns a list of contract
violations (empty when valid).

## Conformance guarantee

This port is verified against the Python engine's frozen oracle on every CI
run: the same pack + the same query must produce a deep-equal result (numbers
within `max(1e-6, 1e-9·|expected|)`). The oracle lives at
[`conformance/quotes.json`](https://github.com/shapemachine/costql/blob/main/conformance/quotes.json)
in the repo, spanning simple, fanout, sharing-heavy, external-cost, and cyclic
low-confidence queries across all five committed demo packs.

Version pinning: `costql@0.1.x` (npm) conforms to `costql` 0.1.x (PyPI),
contract v1.0, `pack_version` 1.

## What this package does not do

Building packs (calibration, measurement, model fitting) is Python-only by
design: that side needs a live API and numerical fitting. See the
[quickstart](https://costql.com/docs/quickstart) for the two-language workflow:
build in Python, quote in Python or JS.

## License

Apache-2.0
