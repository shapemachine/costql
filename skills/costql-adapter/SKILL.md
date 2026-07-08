---
name: costql-adapter
description: Write a costQL adapter for a new GraphQL API: the ~90-line file that lets `costql build` calibrate the API into a pricing pack. Use when the user wants to onboard an API to costQL, price a new GraphQL endpoint, or asks how to write an APIConfig/adapter. Can run autonomously from just an endpoint URL.
---

# Write a costQL adapter

You are onboarding a GraphQL API to costQL. The deliverable is ONE Python file
exporting a factory that returns an `APIConfig`. The engine never changes.
The canonical reference is the docs page `docs/adapters.md`
(https://costql.com/docs/adapters/); the worked examples are
`examples/adapters/rickmorty.py` (minimal, T1; start from this one),
`tmdb.py` (paid bounded field, loaders), and `northwind.py` (batch-width
sweeps). For flags, trust `costql build --help` over any prose.

The only inputs you need from the human are the **endpoint URL** and, if the
API needs it, an **auth header**. Everything else below is discoverable.
If the human hands you curated IDs, use theirs; otherwise harvest your own
(step 2).

## Procedure

1. **Probe, don't guess.**

   ```
   costql probe <url>
   ```

   This reports: whether the endpoint introspects, the **achievable tier and
   `cost_currency` today** (from whether responses carry
   `extensions.cost_trace`, and how much of it), and the **id sources**:
   argument-free root paths that reach id-bearing entities. Copy `tier` and
   `cost_currency` into the adapter exactly as reported. Never hand-pick
   them: T1 = `"wall_time_ms"` (one stopwatch on the whole request), T2/T3 =
   `"work_ms"` (summed per-resolver work), and the probe knows which one the
   server affords. If the probe says T1 but the human says they run the
   server, ask whether tracing is gated (e.g. behind an env var like
   `COSTQL_TIER`); have them enable it and re-probe before settling.

2. **Collect real IDs, never fabricate.** For each major entity pick three
   DISJOINT rows: `whale` (the most densely-connected you can find, worst
   case fanout), `small` (a light one), `heldout` (reserved, never used in
   calibration). If no curated list was provided, harvest one yourself from
   the probe's id sources: run each harvest query (e.g.
   `{ characters { results { id } } }`, paginating a few pages), then rank
   candidates by connectivity: query a handful of ids selecting their
   list-typed fields and count children. Biggest = `whale` candidate,
   a one-child row = `small`, and reserve a mid-sized one you never
   calibrate on as `heldout`. Verify every chosen id resolves on the live
   API before writing it down. Put them in dicts at module top like the
   examples.

3. **Copy the template.** `cp examples/adapters/rickmorty.py <api>.py`, then
   rewrite: `GRAPHQL_URL`, the ID tables, `SIZE_ROOTS` (list fields ->
   {arg, offset, cap}), `default_cap` (= the API's page size; find it by
   asking for `first: 1000` and counting what comes back).
4. **Write the `ArgResolver`.** Route each arg name to the right ID table via
   `field_path` substring matching; return `UNSET` for anything you don't
   handle. Honor `size` ("whale"/"small") and `variant` ("calib"/"heldout").
5. **Write `calibration_queries(size)`.** Clean, PREDICTABLE shapes; how many
   is derived, not fixed: start around 8, add one per resolver you price, and
   >=3 width-sweep shapes per batched edge. Black-box T1 lands near 8-10;
   batching T3 near 20+ (the shipped examples run 8/12/23). Every list edge
   crosses into a DIFFERENT type and never re-enters (no `a{ b{ a } }` cycles:
   those corrupt the fit; costQL flags them at quote time instead). Cover every
   resolver you want priced. If the server batches (T2/T3), sweep each batched
   edge across >=3 declared widths (`first:3/15/40`) so its size->cost curve
   can be fit.
6. **Set fidelity from the probe (step 1), not from hope.** `costql build`
   downgrades to T1 anyway if it observes no trace, so over-claiming just
   wastes a build. Claiming T2/T3 requires the server to emit the cost-trace
   extension (https://costql.com/docs/instrumentation/).
7. **Declare outside-call fields** in `bounded_fields` so they are sampled
   once in isolation instead of fanned out (e.g. an LLM-backed field). costQL
   records the host it observes; it never prices the outside call (the consuming
   app does). See https://costql.com/docs/external-calls/.
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

- Calibration queries erroring (bad IDs, missing auth): the build prints
  each skipped query; fix inputs rather than accepting a thin fit.
- All shapes hitting one resolver: other resolvers get no signal.
- IDs from a test fixture that the live API 404s on.
- Harvested ids all from page 1: they may share a hot cache; spread the
  harvest across a few pages.
- Forgetting `heldout` disjointness: you lose the ability to evaluate
  honestly later.
