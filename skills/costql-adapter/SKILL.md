---
name: costql-adapter
description: Write a costQL adapter for a new GraphQL API — the ~90-line file that lets `costql build` calibrate the API into a pricing pack. Use when the user wants to onboard an API to costQL, price a new GraphQL endpoint, or asks how to write an APIConfig/adapter.
---

# Write a costQL adapter

You are onboarding a GraphQL API to costQL. The deliverable is ONE Python file
exporting a factory that returns an `APIConfig`. The engine never changes.
The canonical reference is the docs page `docs/adapters.md`
(https://costql.com/docs/adapters/); the worked examples are
`examples/adapters/rickmorty.py` (minimal, T1 — start from this one),
`tmdb.py` (paid bounded field, loaders), and `northwind.py` (batch-width
sweeps). For flags, trust `costql build --help` over any prose.

## Procedure

1. **Scout the API.** Introspect it (POST the standard introspection query to
   the endpoint) and note: the root fields, which list fields take a
   pagination arg (`first`/`limit`/`page`), the server's page size, and
   whether auth headers are needed.
2. **Collect real IDs — never fabricate.** For each major entity pick three
   DISJOINT rows: `whale` (the most densely-connected you can find — worst
   case fanout), `small` (a light one), `heldout` (reserved, never used in
   calibration). Put them in dicts at module top like the examples.
3. **Copy the template.** `cp examples/adapters/rickmorty.py <api>.py`, then
   rewrite: `GRAPHQL_URL`, the ID tables, `SIZE_ROOTS` (list fields ->
   {arg, offset, cap}), `default_cap` (= the API's page size).
4. **Write the `ArgResolver`.** Route each arg name to the right ID table via
   `field_path` substring matching; return `UNSET` for anything you don't
   handle. Honor `size` ("whale"/"small") and `variant` ("calib"/"heldout").
5. **Write `calibration_queries(size)`.** 8–15 clean, PREDICTABLE shapes:
   every list edge crosses into a DIFFERENT type and never re-enters (no
   `a{ b{ a } }` cycles — those corrupt the fit; costQL flags them at quote
   time instead). Cover every resolver you want priced. If the server batches
   (T2/T3), sweep each batched edge across >=3 declared widths
   (`first:3/15/40`) so its size->cost curve can be fit.
6. **Set fidelity honestly.** An API you can't instrument is
   `tier="T1"`, `cost_currency="wall_time_ms"`, `known_loaders=[]`. Claim
   T2/T3 only if the server emits the cost-trace extension
   (https://costql.com/docs/instrumentation/) — `costql build` downgrades to
   T1 anyway if it observes no trace, so over-claiming just wastes a build.
7. **Declare paid/external fields** in `bounded_fields` so they are sampled
   once in isolation instead of fanned out (e.g. an LLM-backed field). Their
   per-call fee is authored in the adjustments file, never measured.
8. **Build and verify.**

   ```
   costql build --adapter <api>.py:<factory> --out <api>.json
   costql validate --pack <api>.json
   costql quote --pack <api>.json '<a query the app actually sends>'
   ```

   Sanity checks: a tiny query's price ≈ one real request's latency; a wider
   `first:` never prices LOWER; a deliberately cyclic query returns
   `confidence: low`.

## Failure modes to catch

- Calibration queries erroring (bad IDs, missing auth) — the build prints
  each skipped query; fix inputs rather than accepting a thin fit.
- All shapes hitting one resolver — other resolvers get no signal.
- IDs from a test fixture that the live API 404s on.
- Forgetting `heldout` disjointness — you lose the ability to evaluate
  honestly later.
