---
description: "costQL prices in one currency, work-ms, across three tiers of fidelity. T1 works on any API black-box; T2 and T3 see parallel and shared work through a server trace. The right tier is a fact about your API."
---

# One currency, three fidelities

costQL prices everything in one currency, **work-ms** (the summed duration of the real work a query causes), and offers three *tiers* that are fidelities of one engine, not three different products or units. A tier is simply how sharply the API's instrumentation lets costQL see and factor that work: each tier removes a specific *blur* between what is observable and the true work. The tier your API affords is the correct one — no tier is better than another, and every tier produces the same billable result shape under the same [output contract](contract.md).

## The currency: work-ms, not elapsed time

**Work-ms = the summed durations of the real work a query causes** (downstream
call durations plus local compute), not the wall-clock time the request took.
The distinction matters: a request that fires ten parallel 50 ms calls *elapses*
~50 ms but *costs* ~500 ms of work. Elapsed time is what the caller waited;
work-ms is what the server spent. costQL charges for the latter.

One currency at every tier. T1, which cannot see inside the request, falls back to
wall-clock as a low-fidelity **proxy** for work-ms (its results carry
`currency: wall_time_ms` so the proxy is never silent).

And always cost-units, never dollars: converting cost-units into money is the
consuming app's job.

## The three fidelities

| Tier | What it sees | Blur it removes |
|---|---|---|
| **T1** | one stopwatch on the whole request (wall-clock); per-resolver costs recovered by regression across many queries | none (the starting fidelity) |
| **T2** | each resolver's invocations + each downstream call's duration → summed work-ms | **parallelism**: no longer fooled by concurrent calls; sharing still *inferred* |
| **T3** | the above **plus** each call's key + cache status | **sharing**: dedup/coalescing observed exactly, batch sizes priced on learned curves, outside hosts named |

- **T1 (black box).** Sees only whole-query elapsed time. Work done in parallel or
  in batches hides inside elapsed time, so T1 tends to **under-count**: e.g. a
  genre-hub query whose measured T3 work was 156 ms elapsed as only 87 ms at T1.
- **T2 (per-resolver work).** Sums each resolver's real durations, so parallelism
  no longer hides work. But a call reused by several fields is *guessed*, not
  observed: shared work can be double-counted.
- **T3 (work + sharing trace).** Observes exactly which calls coalesced or hit
  cache; shared calls are counted once and each shared loader is priced by a
  **learned batch-size curve** (a 300-key batch costs more than a 3-key one; a
  database loader's curve rises, a network loader's stays flat: learned, never
  assumed). Outside hosts are named, with the call count; costQL doesn't price the
  outside call (it can't know what that host charges), so the consuming app does.

What each tier's *result* carries (breakdown, sharing, external calls) is
specified and validator-enforced in [the output contract](contract.md).

## What each tier requires, honestly

**T1 works against any GraphQL API, today, with no cooperation from the server.**
The [Rick & Morty case study](results/rickmorty.md) priced a live public API that
we don't own and can't change to **~96% accuracy** with a **93-line adapter and
zero server changes**.

**T2 and T3 require seller-side instrumentation**: the API server must emit
costQL's cost-trace extension (per-resolver timings for T2; plus loader keys and
cache status for T3). The demo packs in this repo are T3 *because we instrumented
the demo servers ourselves*. That is the honest reason they can show observed
sharing.

**Your first pack will be T1, and that is the designed starting point.** It
already gives you the full contract-shaped result with a safe max; adding
instrumentation later moves the same pack pipeline to the fidelity your server
now emits, without changing how your app reads the price.

## Which tier fits your API?

Not a ranking — a fact about your API's shape. Ask, in order:

1. **Can your server emit costQL's cost-trace at all?** If you can't change the
   server, **T1 is the fit**: the only tier that requires nothing from anyone,
   and the honest fidelity for a black box (the
   [Rick & Morty case study](results/rickmorty.md) is exactly this).
2. **Does your server batch, coalesce, or cache reads — or does your schema
   funnel many lookups onto a few hub entities?** Then your API needs the tier
   that watches sharing: **T3**. A tier that never sees the sharing happen
   cannot price those queries, however well it does everything else; that
   mismatch is measured in the
   [Northwind case study](results/northwind.md).
3. **Do your resolvers fire downstream work in parallel inside one request?**
   Then your API needs per-resolver timing: **T2**. With no sharing to observe,
   there is nothing extra for T3 to see — on the lightly-sharing
   [TMDB demo](results/tmdb.md), T2 and T3 priced the billable band
   identically.
4. **Neither?** Serial, unshared work is exactly what one stopwatch measures
   well: **T1 fits**, even though you could instrument. The remaining reason to
   instrument is detail in the quote itself — a per-resolver `breakdown` arrives
   at T2, observed `sharing` and named `external_calls` at T3 (the
   [contract's tier table](contract.md) lists exactly what appears when).

Whatever the destination, your first pack is still T1. That is a sequence, not
a rank.

## One more axis: confidence (orthogonal to tier)

Tier is about the *API's* observability; **confidence** is about the *query's*
predictability. At every tier, a query whose result size is runtime-unknowable
(cyclic recursion, or compounding undeclared list sizes) is flagged
`confidence: low` and priced as a structural ceiling rather than silently billed.
See [Honest limitations](limitations.md).
