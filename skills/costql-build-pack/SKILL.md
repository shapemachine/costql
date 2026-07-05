---
name: costql-build-pack
description: Build (or rebuild) a costQL pricing pack from a live GraphQL API and verify it's sound. Use when the user wants to run `costql build`, regenerate a pack after a schema change, author fee adjustments, or asks why a build downgraded to T1.
---

# Build a pricing pack

Goal: run `costql build` against a live endpoint via an adapter and end with a
verified, quotable pack file. Prereqs: `pip install 'costql[build]'`, an
adapter file (see the costql-adapter skill), and the target API reachable.
For exact flags, run `costql build --help`. Don't trust prose over it.

## Procedure

1. **Start the target.** For the repo demos: see
   `examples/demos/*/README.md` (tmdb needs TMDB keys exported; northwind
   needs its 24 MB reference DB downloaded first). For the user's own API,
   confirm the adapter's `graphql_url` answers a `{ __typename }` POST.
2. **Mind paid fields.** If the adapter declares `bounded_fields` backed by a
   paid host, the build makes a few real paid calls to sample them. The build
   prints the call budget up front. Confirm with the user before large
   `--repeats` values.
3. **Build.**

   ```
   costql build --adapter <api>.py:<factory> --out <api>.json
   ```

   `--repeats N` (default 5) trades build time for measurement stability;
   raise it on noisy hosts. `--tier T1|T2|T3` requests a fidelity from the
   adapter; the build HONESTLY DOWNGRADES to T1 (and says so) when the server
   emits no cost trace, so a surprise "downgraded" line means the
   instrumentation isn't emitting. Check
   https://costql.com/docs/instrumentation/ for the expected
   `extensions.cost_trace` shape.
4. **Author fees (optional).** If bounded fields exist and no
   `--adjustments` file was given, the pack carries a zero-fee template.
   To charge for a paid call: copy the template out of the pack's
   `adjustments` section into a JSON file, set `added_unit_cost` (COST-UNITS,
   never dollars), and rebuild with `--adjustments <file>`.
5. **Verify before shipping.**

   ```
   costql validate --pack <api>.json
   costql quote --pack <api>.json '<typical app query>'
   ```

   Expect: resolver-cost count roughly matching the schema's non-leaf
   resolvers; tier/currency as intended; a tiny query priced in the same
   ballpark as one real request; cyclic shapes flagged `low`.
6. **Ship.** The pack is a plain static file: vendor it into the consuming
   app (Python `PricingPack.load`, JS `PricingPack.fromObject`). No service.

## Rebuilds

Rebuild whenever the schema changes (quotes carry `schema_hash`; a consumer
comparing hashes will notice drift) or when real costs shift materially
(infra change, new data distribution). Rebuilding is cheap (minutes of
measurement) and the adjustments file survives via `--adjustments`.
