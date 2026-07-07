---
name: costql-build-pack
description: Build (or rebuild) a costQL pricing pack from a live GraphQL API and verify it's sound. Use when the user wants to run `costql build`, regenerate a pack after a schema change, handle a field that calls an outside host, or asks why a build downgraded to T1.
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
2. **Mind outside-call fields.** If the adapter declares `bounded_fields` backed
   by an outside host (e.g. a paid LLM), the build makes a few real calls to
   sample them and records the host it observes. The build prints the call budget
   up front. Confirm with the user before large `--repeats` values.
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
4. **Outside calls are named, not priced.** If bounded fields call an outside
   host, the pack records that host (in `model.external_hosts`) and a T3 quote
   surfaces it as `external_calls` (host + call count, no fee). costQL never
   prices the outside call: the consuming app does. Nothing to author. See
   https://costql.com/docs/external-calls/.
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
measurement); observed outside hosts are re-detected each build.
