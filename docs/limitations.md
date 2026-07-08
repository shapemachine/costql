---
description: "Where costQL's typical estimate is least certain — cyclic-recursion and data-dependent queries. In every case the billable ceiling stays safe: costQL degrades gracefully to a priced quote plus an honest confidence tag, never a refusal."
---

# Honest limitations

costQL gives you **two numbers per quote, and they fail differently.** The *typical* estimate is a best guess for a normal-sized case. The **billable ceiling** is the safe maximum — it never under-prices, and it is the number costQL bills on. Every soft spot below can bend the *typical* estimate on some hard query. In each one the **ceiling still holds**, and costQL tags the query `confidence: low` so you know to lean on the ceiling, not the typical.

So read these as limits on the typical estimate's *precision*, not on costQL's guarantee — a pricing tool you can't trust about its own blind spots isn't worth trusting about prices. The one exception is called out where it lives (the single-resolver size dimension below), because honesty is the whole point of this page. Everywhere else, the designed behavior is **graceful degradation, never refusal**: a contract-valid price plus an honest confidence tag, every time.

## Cyclic-recursion queries

A query that re-enters a type through a list edge (movie → recommendations →
recommendations…; character → episodes → characters…) fans out combinatorially,
and the real backend de-duplicates by an amount only *running* the query reveals.
costQL does not fabricate a dedup guess. It prices the query **structurally** (a
safe max), flags it `confidence: low`, and attaches a caveat: run it once for
the exact cost. On the [TMDB demo](results/tmdb.md), the 4 cyclic held-out queries
averaged ~92% error on the typical estimate — which is exactly why the typical is
flagged, not trusted as the price. The number you are billed is the structural
safe max, and it stays above the real cost. On
[Rick & Morty](results/rickmorty.md), every loop-shaped query was auto-flagged.

## Data-dependent result sizes

A query with **two or more un-paginated list edges compounding on one path**
("this customer's orders, and every line-item of each") has a cost that depends on
the data (how many orders *that* customer has), not just the query's shape. The
confidence classifier detects this pattern and returns `low` with a "declare sizes
or run it" caveat. The typical estimate can drift (a ~39% miss on the worst such
query in the [Northwind study](results/northwind.md)); the **ceiling stays safe**
(verified there: 2.65 vs a real 1.95–2.40 across every customer, including the
heaviest). Declaring sizes (pagination arguments) restores high confidence.

## T1-only (black-box) APIs

If an API emits no cost-trace instrumentation, costQL still prices it. That is
the T1 fidelity, and it is [the designed starting point](tiers.md). The
difference is in *detail*, not in the guarantee: a T1 result carries the total
only (`currency: wall_time_ms`, a wall-clock proxy for work-ms) with no
per-resolver `breakdown`, no observed `sharing`, and no `external_calls`. Work
hidden by parallelism or batching is not decomposed and tends to be
under-counted in the proxy. Measured honestly, T1 still performed well where the
cost is dominated by what a black-box caller actually experiences: ~96% accuracy
on the public [Rick & Morty API](results/rickmorty.md).

## When a field's cost grows with an argument

costQL learns how a cost grows with size by **measuring the same field at several
sizes** during calibration. It does this for the two size-sensitive shapes that
dominate real APIs — and that writing an adapter naturally exercises: **batched
loaders** (a shared read's cost against how many rows it pulls; the
[Northwind study](results/northwind.md) fit this curve and verified it stays
ceiling-safe under heavy sharing) and the **list fields** you already vary
`first:` / `limit:` across in your calibration queries.

One shape isn't swept by default: a **single resolver whose own per-call work
grows with an argument** — a field that takes, say, `limit: 500` and does real
work on all 500 rows in a single call. If calibration only ever measured it
small, costQL prices it flat, and a much larger request against it can come in
low.

Two reasons this is a corner, not a cliff:

- **In every case study, its measured effect was ~0%.** On passthrough-style APIs
  the list items ride inside the parent's single fetch, so there is no per-item
  cost to scale. It takes an uncommon shape — a field doing heavy per-item local
  work — to matter at all.
- **The fix is one calibration query, not a redesign.** If such a field ever
  surfaces, list it in your calibration queries at a large argument, name its
  size argument (a [`size_root`](adapters.md)), and rebuild the pack. Building is
  offline and one-time; the pack is just a file you regenerate. From then on the
  quote scales correctly.

The honest version: costQL prices what your calibration exercises. The
size-sensitive fields you would naturally reach for are swept for you; the rare
one you would not think of is a cheap, known thing to fold in the moment it shows
up.

## Polymorphic branches price as an upper bound

Before a query runs, nobody knows which `... on Type` branch each object
resolves to, so [the quote](quoting.md) walks every branch. At most one fires
per object, so the price is a safe max, and the quote says so in a caveat
naming the branched paths. Run the query for the exact cost.

## A missing variable value prices at the worst case

A `$variable` with no supplied value and no declared default loses its
argument, so that field prices at the ceiling's worst-case bound: possibly
higher than needed, never an under-price. Passing values
([`quote(query, variables)`](quoting.md)) restores the exact bound.

## Non-goals (by design, not omission)

- **No hosted service.** The pricing pack is a static, local file; there is no
  sidecar, pricing endpoint, or extra API call in the quote path. See
  [the architecture](architecture.md).
- **No dollars, no billing.** costQL speaks cost-units only; the consuming app
  owns the single rate that turns cost-units into money.
- **No load or traffic model.** A quote prices **one execution** of a query. What
  you multiply that by — requests per second, concurrent callers, total volume —
  is your own infrastructure dimension. Pricing *that* (a rate limit, a throughput
  tier) is the API owner's call, and costQL leaves it to you.
- **No buyer-facing transparency mechanism.** How much of a quote's breakdown a
  seller shows their customers is the seller's design call, not costQL's.
