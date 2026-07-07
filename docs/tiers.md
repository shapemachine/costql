# One currency, three fidelities

costQL prices everything in one currency, **work-ms** (the summed duration of the real work a query causes), and offers three *tiers* that are fidelities of one engine, not three different products or units. A tier is simply how sharply the API's instrumentation lets costQL see and factor that work: each step up removes a specific *blur* between what is observable and the true work. The highest tier your API affords is the correct one, and every tier produces the same billable result shape under the same [output contract](contract.md).

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
| **T1** | one stopwatch on the whole request (wall-clock); per-resolver costs recovered by regression across many queries | none (coarsest) |
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
already gives you the full contract-shaped result with a safe max;
adding instrumentation later upgrades the same pack pipeline to sharper fidelity
without changing how your app reads the price.

## When is T3 worth the instrumentation?

Measured answer: **it depends on how much your schema shares entities.**

- **Light sharing → T2 is enough.** On the [TMDB demo](results/tmdb.md), T1/T2/T3
  scored 17% / 11% / 11% mean error on the billable band. The T2→T3 gap was ~0.
- **Heavy sharing → T3 pays, a lot.** On the
  [batch-heavy Northwind database](results/northwind.md) (coalescing factor 38.5×),
  light-sharing queries again tied (T2 6% ≈ T3 6%), but on heavy-sharing hub
  queries T2 erred **315%** vs T3's **75%**, and **12%** once batched reads were
  priced on the learned size curve. T2 cannot price coalesced reads because it
  never watches the sharing happen; that error is irreducible at T2.

The rule of thumb: if one query in your schema can re-request the same few hub
entities dozens of times (and your server batches those reads), T3 instrumentation
buys you real accuracy. If your resolvers mostly fetch distinct things once, the
middle tier already prices them well.

## One more axis: confidence (orthogonal to tier)

Tier is about the *API's* observability; **confidence** is about the *query's*
predictability. At every tier, a query whose result size is runtime-unknowable
(cyclic recursion, or compounding undeclared list sizes) is flagged
`confidence: low` and priced as a structural ceiling rather than silently billed.
See [Honest limitations](limitations.md).
