---
description: "npm install costql gives you the quote side in JavaScript/TypeScript: load a pack and price queries offline in Node, browsers, or edge. Zero runtime deps."
---

# The JS package

`npm install costql` gives you the **quote side** of costQL in
JavaScript/TypeScript: load the same pack file the Python engine writes and
price queries offline in Node, browsers, or edge runtimes. Zero runtime
dependencies. Building packs stays in Python (`pip install 'costql[build]'`)
by design: that side needs a live API and numerical fitting; quoting is pure
traversal and belongs everywhere your app runs.

## Usage

```ts
import { PricingPack, validate } from "costql";

// Node
const pack = await PricingPack.load("packs/tmdb_t3.json");

// Browser / edge: fetch the pack like any static asset
const pack = PricingPack.fromObject(await (await fetch("/packs/tmdb_t3.json")).json());

const quote = pack.quote('{ movie(id:"27205"){ cast(limit:8){ person{ name } } } }');
quote.price           // 50.5384 (safe max, cost-units)
quote.typical_price   // 36.3769 (typical everyday cost)
quote.confidence      // "high" | "medium" | "low"
quote.caveats         // e.g. the cyclic-recursion explanation, when flagged
quote.breakdown       // per-resolver cost lines (T2/T3 packs)
quote.sharing         // folded shared loaders (T3 packs)
quote.external_calls  // named outside hosts + call count (T3 packs)

validate(quote)       // [] (contract v1.0 violations, if any)
```

The result object is the [frozen output contract v1.0](contract.md): the
identical shape the Python engine emits, tier-gating included.

`PricingPack.fromObject` / `fromJSON` throw `PackVersionError` on a pack
written by a newer costql, a missing section, or a non-pack, the same gate as
the Python loader.

## The conformance guarantee

"Identical to the Python engine" is a tested claim, not a hope. The repo
freezes an oracle ([`conformance/quotes.json`](https://github.com/shapemachine/costql/blob/main/conformance/quotes.json),
56 full quote results spanning simple, fanout, sharing-heavy, external-call,
cyclic, and sugared (fragments/aliases/variables) queries across the five demo
packs plus a synthetic union-schema pack) and CI runs both engines
against it on every PR. Numbers must agree within
`max(1e-6, 1e-9 · |expected|)`; every other field must be deep-equal. Even the
caveat *strings* match.

Pinning: `costql@0.1.x` (npm) conforms to `costql` 0.1.x (PyPI), contract
v1.0, `pack_version` 1.

## Shared parser, shared behavior

The JS parser is a deliberate line-for-line port of the Python one, so the
two engines accept the same query surface (fragments, aliases, variables,
directives: see the [parser notes](limitations.md)) and price it identically.
To pass variable values, hand them to `quote` directly:

```ts
pack.quote('query($n: Int!){ movie(id:"27205"){ cast(limit:$n){ person{ name } } } }',
           { n: 4 });
```

If you hit an edge where the two engines disagree, that is a bug. Please file
it with the pack + query.

## What's deliberately absent

- No `build`/calibration APIs: Python-only.
- No network access in `quote()`: a pack in, a contract result out.
- No dollars: cost-units only; currency conversion is your app's concern.
