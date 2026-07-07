---
description: "A batch-heavy database backend where entity sharing is heavy, so T3 pulls far ahead of T2. Measured against a real SQLite store, no fabricated numbers."
---

# Case study: a batch-heavy database backend

On the [TMDB demo](tmdb.md), the middle fidelity (T2) priced queries just as well as the top fidelity (T3), but TMDB shares entities only lightly. To find out whether "the middle tier is enough" is a universal claim, we pointed costQL at a very different backend: a real **Northwind** store database (products, orders, customers, SQLite), deliberately shaped so a single query hammers the same tiny set of entities over and over. The finding: **the two tiers stay tied when sharing is light and pull far apart when sharing is heavy**. T3's payoff scales with how much the schema shares. Everything below is measured against the real database: real SQL, real rows, no fabricated numbers, zero paid calls.

## What we tested it on

Northwind funnels a lot onto a little: **only 8 product categories and 77
products**, but **609,000 order-lines**. So a query like "for these 40 orders,
each line-item's product's category" asks for a category **hundreds of times**
while there are only 8 of them. A store database that batches its reads (as this
one does, and as most real ones do) serves all those repeats from a handful of
actual reads. That repetition is the "sharing," and it's the whole point.

We priced a set of held-out queries the engine had never seen
([methodology](../evaluation.md)), split into a predictable ("billable") band and
flagged-uncertain ones, and compared each tier's predicted cost against the real
measured cost.

## How much sharing actually happened

Across the predictable held-out queries, the requests genuinely piled onto a few
entities and the database coalesced them:

| measured across the billable held-out set | value |
|---|---|
| entity reads the queries **asked for** | **963** |
| distinct rows actually **fetched** | 405 |
| real **SQL queries** run (after batching) | **25** |
| reads **saved** by coalescing | **558** |
| coalescing factor (asked ÷ SQL queries) | **38.5×** |

A single hub query asked for a category **107 times and the database read it
once** (8 distinct categories, 99 repeats served from cache). This is heavy
sharing, not a token amount, so the tier comparison is a fair test of the claim.

## The result: the tier gap is sharing-dependent

On the predictable, billable queries, mean error vs. real measured cost, first
with the naive sharing rule that simply folds every shared read to "counted once":

| | middle tier (T2) | top tier (T3, flat fold) | gap |
|---|---:|---:|---:|
| **light-sharing queries** | **6%** | **6%** | **~0%** |
| **heavy-sharing queries** | **315%** | **75%** | **+239%** |
| all predictable (mixed) | ~160% | ~41% | ~+120% |

- **When sharing is light, the middle tier is enough**: it matches the top tier
  to within a rounding error. This reproduces the TMDB result on a completely
  different, database-backed schema.
- **When sharing is heavy, the two tiers split wide open.** The middle tier,
  which doesn't watch the sharing happen, charges for *every* repeated request
  and over-prices by ~3×. The top tier, which *observes* the coalescing, is far
  closer. On TMDB the T2→T3 gap was ~0; here it reaches **+239%** on the heaviest
  queries. **The gap widens with sharing.**

## Why "counted once" isn't enough for a database, and the batch curve that fixes it

Even with the gap established, the flat fold left the top tier imperfect, and in
the **unsafe** direction: on the heaviest queries it **under-priced** (predicting
~0.04 vs a real ~0.33). The reason is specific. On TMDB, "sharing" only ever meant
"the same single item asked for twice," so counting it once was exactly right. A
store database instead reads *many different* rows in **one** batched SQL query,
and that one query costs more the more rows it pulls.

So costQL prices each shared loader with a **learned batch-size curve**: from the
calibration measurements it fits each read's cost as a function of how many rows
the batch pulls, and prices a batch at that curve. A database's loaders come out
on a **rising** curve; a network API's come out **flat**: learned from data,
never assumed (nothing hard-codes "database vs. network"). The four heaviest
hub-fanning queries, measured:

| query | real cost | T3, flat fold | T3, size-aware curve |
|---|---:|---:|---:|
| customer → orders → lines → products | 2.72 | 1.50 (−45%) | 1.66 (−39%) |
| orders → lines → product → category | 0.32 | 0.03 (**−89%**) | 0.31 (−5%) |
| orders → lines → product → supplier | 0.28 | 0.04 (**−86%**) | 0.29 (−4%) |
| orders → lines → product → cat+supplier | 0.27 | 0.03 (**−87%**) | 0.27 (−2%) |

The pagination-bounded batches went from **~88% under-priced to within ~5%**, and
the pack's **ceiling is genuinely safe** on them (it sits above the real cost,
where the flat fold sat ~10× below). Re-measuring the whole predictable band:

| | middle tier (T2) | T3, flat fold | T3, size-aware curve |
|---|---:|---:|---:|
| light-sharing queries | 11% | 13% | **10%** |
| heavy-sharing queries | 346% | 77% | **12%** |
| all predictable | 178% | 45% | **11%** |

(These re-measured T2 figures differ slightly from the first table's run: live
measurement, two separate runs. The shape is identical.)

Note what the fix did *not* do: it did not shrink the tier gap. The middle tier
genuinely cannot price coalesced reads, because it never watches the sharing.
That is real, irreducible tier value. Fixing the fold made the top tier accurate,
which made the measured gap **wider** (+269% → +334% on the heavy queries): the
flat rule had been *understating* T3's advantage by crippling T3 itself.

## The honest residue

One held-out query still misses by ~39% on its typical estimate: "for this
customer, all their orders and line-items." Its cost depends on **how many orders
that specific customer has**, knowable only by running the query, not from its
shape. costQL handles this the same way it handles cyclic queries: the confidence
classifier detects the pattern (two or more un-paginated list edges compounding on
one path), returns **low confidence** with a "declare sizes or run it" caveat, and
the ceiling stays safe: **2.65 vs a real 1.95–2.40 across every customer**,
including the 210-order heaviest one (the 77-product / 8-category hubs bound the
batch), so there was never an under-billing risk. Queries whose batch size is
fixed by the query itself (pagination, or a small hub table like the 8 categories)
are priced accurately and stay high-confidence. See
[Honest limitations](../limitations.md).

## No regression on the network API

Re-measured on TMDB: every network-loader curve fits **flat** (a batched call ≈ a
single call there), so the size-aware step adds nothing and T3 is unchanged. The
T2→T3 gap stays ~0 (−0.3%), and packs still pass the frozen contract with 0
violations. The batch curve kicks in exactly where it should (a real database that
batches many rows) and is inert where it shouldn't matter (a network passthrough).

## And costQL generalized, again

Onboarding this third, non-movie, database-backed schema took only a ~90-line
adapter ([`examples/adapters/northwind.py`](../../examples/adapters/northwind.py))
plus the demo server itself; not a line of the shared pricing engine changed. On
this schema the engine built a working cost model from a small calibration panel,
priced unseen queries, automatically flagged the unpredictable (cyclic) ones as
low-confidence, saw and reported the 38.5× coalescing through the same observation
path it used on TMDB, and produced **static, self-contained pricing packs at all
three tiers** ([`packs/northwind_t1.json`](../../packs/northwind_t1.json),
[`packs/northwind_t2.json`](../../packs/northwind_t2.json),
[`packs/northwind_t3.json`](../../packs/northwind_t3.json)), each passing the
[frozen output contract](../contract.md) with **zero violations**, and each
quoting queries **with the server switched off**.

## Bottom line

1. **"The middle tier is enough" is not universal.** It holds when a schema shares
   entities lightly (TMDB, and Northwind's light queries) and fails when a schema
   shares heavily (Northwind's hub queries), where observing the sharing is worth
   a lot. This is a measured number, not an assertion: T3's payoff scales with how
   much the schema shares.
2. **Batched reads are priced by size, safely.** A batch of *N* distinct rows is
   priced on a learned size→cost curve, closing the under-pricing where the batch
   size is knowable and restoring the ceiling guarantee on database backends.
3. **The engine generalizes.** A third real API, a different kind of backend, one
   small adapter, contract-valid offline packs at all three tiers.

## Reproducing it

The demo server lives at [`examples/demos/northwind`](../../examples/demos/northwind)
(it needs a one-command 24 MB reference-database download to run; see its README);
building against it with the adapter above regenerates the packs. Quoting the
committed packs requires nothing:

```bash
costql quote --pack packs/northwind_t3.json \
  '{ order(id:"15000"){ details(first:15){ product{ category{ name } } } } }'
```
