---
title: "Price Pack Format"
---


A **pricing pack** is the single file costQL produces, and the only thing your
app needs to price queries. It is fully self-contained: the schema, the fitted
costs, the observed sizes, and any authored fees, with no server, no sidecar, and
no network call. This page documents what is inside one, so you can inspect, diff,
or debug a pack directly.

You never hand-edit a pack (except the [authored fees](paid-fees.md)); apps read
it through `PricingPack.load()`. What the pack *produces*, the quote result, is a
separate shape specified in [the output contract](contract.md). This page is the
other half: what goes *in*.

## Top-level keys

| key | type | what it is |
|---|---|---|
| `pack_version` | number | pack format version (currently `1`). |
| `schema_hash` | string | a fingerprint of the API's schema. Every quote echoes it, so a consumer can detect that a pack no longer matches the live schema. |
| `tier` | string | `"T1"`, `"T2"`, or `"T3"`: the fidelity this pack was built at (see [tier fidelity](tiers.md)). |
| `currency` | string | the cost-unit every price is in: `"work_ms"` (T2/T3) or `"wall_time_ms"` (the T1 wall-clock proxy). Never dollars. |
| `introspection` | object | the API's GraphQL introspection result, stored so the pack can walk the schema and price any query with no live endpoint. |
| `model` | object | the cost model: the actual pricing payload (below). |
| `adjustments` | object | hand-authored per-call fees for paid or external resolvers (see [Paid & external fees](paid-fees.md)). `{}` when there are none. |
| `note` | string | a human-readable reminder of what the pack is (offline, cost-units, never dollars). |

## Inside `model`: the pricing payload

This is where a query's price actually comes from. Every price is computed from
these fields alone. The pack stores no per-query prices and does no lookup:
`price = sum over the query of (unit_cost + any fee) x invocations`.

| field | type | what it does |
|---|---|---|
| `unit_cost` | `{resolver_id: number}` | the cost of one invocation of each resolver, in cost-units. The core of every price. |
| `typical_size` | `{resolver_id: number}` | the **average** list size observed for each list-returning resolver during calibration. Price the query with these and you get the **typical** number. |
| `max_size` | `{resolver_id: number}` | the **worst-case** (largest) list size observed. Price with these and you get the **safe max**. Typical vs safe max is exactly this one swap of sizes (see [the FAQ](faq.md)). |
| `default_cap` | number | the list size assumed for an edge that declares no size and was never observed. A conservative fallback. |
| `safety` | number | a calibration-derived multiplier (`>= 1`) applied so the safe max never lands below a measured calibration cost. |
| `noise_buffer_ms` | number | a small additive allowance for measurement noise, in ms. Usually `0`. |
| `batch_groups` | `{resolver_id: loader_key}` | **T3 only.** Which resolvers are served by a shared loader, so their repeated work is counted once. Empty at T1/T2. |
| `loader_fns` | `{loader_key: curve}` | **T3 only.** The learned batch-size curve for each shared loader (below). Empty at T1/T2. |
| `scan_before_paginate` | `[resolver_id]` | resolvers whose backend scans a full set before paginating, so a small page can still cost like a large scan. Usually empty. |
| `uncovered_edges` | `[resolver_id]` | schema edges the calibration panel did not exercise, listed for honesty. Usually empty. |
| `cost_currency`, `schema_hash` | | mirror the top-level `currency` and `schema_hash`; the model is self-describing. |

### A `loader_fns` curve (T3)

Each shared loader carries a small curve learned from calibration: cost as a
function of how many distinct keys a batch pulls.

```json
"<loader key>": {         // e.g. a database loader that pulls N rows per batch
  "root":   "<loader key>",
  "kind":   "linear",     // const or linear: the shape fit from the size sweep
  "base":   12.5,         // cost at the smallest batch
  "slope":  0.4,          // added cost per extra distinct key
  "cap":    12,           // largest batch size ever observed (clamps the input)
  "safety": 1.2,          // multiplier keeping the curve a safe upper bound
  "typical": 9.0,         // the typical batch size
  "points": [[1, 12.5], [8, 15.7]]   // the measured (size, cost) samples it was fit from
}
```

A network loader tends to fit **flat** (`const`): a batched call costs about the
same as a single one. A database loader fits a **rising** curve: a 300-key batch
costs more than a 3-key one. Which one it is comes from the data, never assumed.

## What differs by tier

- **T1** (black box): `unit_cost`, `typical_size`, `max_size`, `default_cap`, and
  `safety`, in `wall_time_ms`. `batch_groups` and `loader_fns` are empty; a black
  box affords no view of shared work.
- **T2** (per-resolver work): the same shape in `work_ms`, with per-resolver costs
  that no longer hide parallel work. Sharing is still inferred, so no observed
  `loader_fns`.
- **T3** (work plus sharing trace): adds `batch_groups` and `loader_fns` (observed
  coalescing, priced once on the learned curve), and names paid hosts in
  `adjustments`.

## Inspecting a pack

A pack is plain JSON. To see a resolver's cost and observed sizes:

```bash
python -c "import json; m=json.load(open('packs/tmdb_t3.json'))['model']; \
  print(m['unit_cost']['Movie.cast'], m['typical_size']['Movie.cast'], m['max_size']['Movie.cast'])"
```

Or validate the result a pack produces against the frozen shape with
`costql validate --pack <pack.json>` (see [the output contract](contract.md)).
