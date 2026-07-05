---
name: costql-quote-debug
description: Interpret and debug costQL quote results: price vs typical, confidence levels, caveats, per-resolver breakdown, sharing folds, external fees. Use when the user asks why a quote is high/low/flagged, what a costQL result field means, or whether a price is safe to bill on.
---

# Interpret a costQL quote

A quote is a frozen contract v1.0 result (reference:
https://costql.com/docs/contract/). Get one with
`costql quote --pack <pack> --json '<query>'` for the raw object.

## Reading the result

- **`price`**: the safe billable CEILING. It is the number to bill/budget
  on; it never under-prices by design (safety-calibrated). Cost-units
  (`currency`), never dollars.
- **`typical_price`**: the fair average estimate. Show it to users; don't
  bill on it.
- **`confidence`**: how predictable THIS query's price is (orthogonal to
  tier): `high` = trust both numbers; `medium`/`low` = the ceiling is still
  safe but typical may drift; `exact` = a measured receipt, not a prediction.
- **`caveats`**: always read the first one; it names the failure mode and
  the remedy in plain language.
- **`breakdown`** (T2/T3): per-resolver cost lines, sorted descending =
  the cost drivers. `invocations` shows the fanout multiplication.
- **`sharing`** (T3): resolvers folded onto one shared loader, counted once.
- **`external_costs`** (T3): named paid hosts with their AUTHORED fee
  (measured_fee is false: fees are seller-authored, never measured).

## Common "why is it…" answers

- **…flagged low confidence?** Almost always cyclic recursion (a type
  re-enters itself through a list edge, `recommendations{ recommendations }`)
  or compounding UN-DECLARED list sizes. Remedy per the caveat: declare sizes
  (`first:N`) for an exact quote, or run the query for the exact cost.
- **…not scaling when I raise `limit:N`?** If the child work folds onto a
  flat-curve loader (per-id HTTP calls), a wider page rides the same batched
  read. That's observed sharing working, not a bug. DB-backed rising curves
  DO scale (compare the Northwind hub examples).
- **…so high?** Check the top `breakdown` line and its `invocations`: usually
  an undeclared list priced at its worst observed size. Declaring the size
  drops the ceiling to the declared bound.
- **…zero-ish for a field I know is expensive?** Leaf scalar fields carry ~0
  cost by design (attribute reads); cost concentrates on the resolvers that
  do work. A leaf that secretly does heavy compute belongs in the adapter's
  `bounded_fields` or needs T2 instrumentation to be seen.
- **…different between the Python and JS packages?** It must not be: they
  are conformance-locked. Any real divergence is a bug; capture pack + query
  and file it.

## Sanity workflow

1. `costql validate --pack <pack>`: pack readable, complete, right tier.
2. Quote a trivial query; it should approximate one request's real latency.
3. Quote the disputed query with `--json`; read caveats, then breakdown.
4. If prediction vs reality genuinely diverges on a high-confidence query,
   the pack is stale: rebuild (schema_hash tells you if the schema moved).
