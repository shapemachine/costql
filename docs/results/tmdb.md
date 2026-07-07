---
description: "Three-tier accuracy on the TMDB demo. On this lightly-sharing API there is almost no shared work to watch, so per-resolver timing (T2) and the sharing trace (T3) price it the same."
---

# Case study: three-tier accuracy on the TMDB demo

Measured against the **real** TMDB demo API (live data, work-ms currency, zero Anthropic calls), this experiment answers a standing question head-on: **does this API's shape need the sharing trace (T3), or does per-resolver timing (T2) already see everything that happens?** On this lightly-sharing passthrough API the two fidelities tie — there is almost no shared work for T3 to watch. See the [Northwind case study](northwind.md) for an API shape where there is.

## How it was measured

- **Robust held-out panel, disjoint from calibration by entity** (calibration uses
  Inception/DiCaprio/Action + Fight Club/Pitt/Drama; held-out uses
  Interstellar/Cruise/Comedy/"star"). Held-out queries are *never* seen at
  build time: the numbers grade prediction of unseen data, not memorisation.
  (Full methodology: [evaluation](../evaluation.md).)
- **Split by confidence band.** 9 predictable (distinct-type fanout) high-confidence
  queries (the billable band) and 4 cyclic (recommendations-of-recommendations,
  genre-hub) low-confidence queries that get *flagged*, not billed.
- **Priced three ways against the real measured cost:**
  - **T1** (black-box): only whole-query wall-clock, regressed onto resolvers.
  - **T2** (per-resolver work): sharing *inferred* (no dedup credit).
  - **T3** (work + sharing trace): sharing *observed* (dedup folded).
- **Average-size pricing.** A request that doesn't declare its size is priced at the
  *typical* observed size, not the worst case, so the comparison is not polluted by
  worst-case over-charging. Where the caller declares a size (`limit: N`), it is
  priced exactly.

## Result: predictable (high-confidence) queries, n = 9

| tier | what it needs from the API | mean error vs real cost |
|---|---|---:|
| **T1** black-box | nothing (wall-clock only) | **17%** |
| **T2** per-resolver work | the cost-trace extension | **11%** |
| **T3** work + sharing trace | the cost-trace extension + loader keys | **11%** |

**T2 → T3 difference: ~0 (−0.8%, within measurement noise).**

**On this API, T2's sight already covers everything that happens to quotable
queries.** With almost no shared work in the schema, observing sharing exactly
(T3) has nothing extra to see before execution. The sharing trace matters only on
the deep/repeated-load queries that *no* tier can quote pre-execution anyway, and
those get an exact price the moment they actually run.

One important scope note: TMDB shares entities only *lightly*. Under heavy entity
sharing, only the tier that watches the sharing can price the work. That is the
[Northwind result](northwind.md), and it is why the tier an API needs
is a property of its schema, not a universal claim.

## On result-size effects

Average-size pricing is the basis for these numbers. On this passthrough API the
isolated worst-case-vs-average size effect is **~0%**: a passthrough returns list
items inside the parent's single fetch, so the marginal work of one more item is
essentially zero. Size uncertainty bites hardest on APIs that do *real* per-item
work (scan-before-paginate), which is exactly the case the work-ms currency is
designed to catch. Larger apparent over-charges in early measurement rounds traced
not to size but to fitting the model on cyclic-recursion queries, whose
combinatorial work totals don't fit a linear model; calibrating on clean,
predictable queries (and flagging the cyclic ones instead of fitting them) removes
the artifact entirely.

## Cyclic (low-confidence) queries, n = 4

Priced structurally and **flagged** (mean error ~92%: recommendations-of-
recommendations fan out combinatorially, and the real backend de-duplicates by an
amount only running the query reveals). costQL does not fabricate a dedup guess; it
prices structurally, tags the query low-confidence, and says "run it for the exact
cost." This is the honest handling, not a modelling failure. See
[Honest limitations](../limitations.md).

## Reproducing it

The committed pack is [`packs/tmdb_t3.json`](../../packs/tmdb_t3.json). The demo
server ([`examples/demos/tmdb`](../../examples/demos/tmdb)) and its adapter
([`examples/adapters/tmdb.py`](../../examples/adapters/tmdb.py)) reproduce the
setup: the server emits all three fidelities from one instrumentation
(`COSTQL_TIER=T1|T2|T3` gates how much of the cost trace it publishes), and
`costql build` against it at each tier regenerates the comparison. Note the demo
requires free TMDB API keys; quoting the committed pack requires nothing.
