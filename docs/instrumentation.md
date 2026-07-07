---
description: "The one response extension (extensions.cost_trace) your GraphQL server emits to unlock costQL sharper T2 and T3 prices."
---

# Instrumenting your server for T2/T3

T1 needs nothing from your server: costQL times whole requests from the
outside. The richer tiers need the server to *tell* costQL what work a request
actually caused, via one extension field on the GraphQL response. This page
specifies that seam. The two demo servers
([`examples/demos/tmdb`](https://github.com/shapemachine/costql/tree/main/examples/demos/tmdb),
[`examples/demos/northwind`](https://github.com/shapemachine/costql/tree/main/examples/demos/northwind))
are complete reference implementations (Strawberry/Python, ~150 lines of
tracing each), but the seam is JSON over HTTP. Any server language can emit it.

## The seam: `extensions.cost_trace`

```json
{
  "data": { "...": "..." },
  "extensions": {
    "cost_trace": {
      "work_ms": 48.7,
      "resolver_work_ms": { "Query.movie": 12.1, "Movie.cast": 36.6 },
      "invocations": { "Query.movie": 1, "Movie.cast": 1, "Credit.person": 8 },
      "loaders": {
        "/person/{id}": {
          "requested_keys": 8,
          "batch_calls": 1,
          "batched_keys": 8,
          "cache_hits": 0,
          "work_ms": 31.2
        }
      }
    }
  }
}
```

## What each tier requires

**T2: per-resolver work.** Emit `work_ms`: the **summed duration of the real
work** the request caused (downstream call durations + local compute), *not*
wall-clock elapsed time. Concurrent calls add their durations. Emit
`invocations` (how many times each resolver fired) and, if you can,
`resolver_work_ms`. This removes the parallelism blur: work hidden inside
concurrent elapsed time becomes visible.

**T3: observed sharing.** Additionally emit `loaders`: one entry per batching
loader (DataLoader, SQL `IN (...)` reader, HTTP client with request
coalescing), with

- `requested_keys`: how many times resolvers *asked* for a row this request,
- `batch_calls`: how many physical calls actually went out,
- `batched_keys`: distinct keys actually fetched,
- `cache_hits`: asks served from the per-request cache,
- `work_ms`: that loader's own summed time.

From this costQL learns which resolvers fold onto shared loaders (so repeated
work is counted once) and fits each loader's **batch-size → cost curve** (a
flat curve for per-id HTTP calls, a rising one for `IN`-list SQL: learned,
never assumed). Declare your loader IDs in the adapter's `known_loaders` so a
loader that never fires is reported as dead.

## Practical notes

- **Keying:** loader IDs are arbitrary strings. Endpoint templates
  (`"/person/{id}"`) or table names (`"products:byCategory"`) both work; they
  just have to be stable.
- **Overhead:** the demos time with monotonic clocks around existing loader
  calls; the trace adds microseconds and is only assembled when tracing is on.
- **Gating:** emit the trace only for calibration traffic if you like. The
  demo servers gate on a `COSTQL_TIER` env var. Packs are built rarely (once
  per schema change); production requests never need the extension unless you
  also want exact post-execution receipts.
- **Honesty is automatic:** if `costql build` sees no `work_ms` in responses,
  it downgrades the pack to T1 and says so. An adapter cannot claim fidelity
  the server doesn't emit.

## Do you need this?

Only if your schema **shares entities heavily**. Under light sharing the
inferred middle tier already matched T3 (TMDB: T2 11% vs T3 11% mean error);
under heavy sharing the gap was decisive (Northwind hub queries: T2 315% vs
T3 12%). Read [the Northwind case study](results/northwind.md), then decide.
Start at T1 either way. It ships today.
