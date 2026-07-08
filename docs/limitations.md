---
description: "Where costQL is least certain: cyclic-recursion and data-dependent queries. In every case it degrades gracefully to a safe price plus an honest confidence tag."
---

# Honest limitations

costQL's core guarantee (a billable `price` on every query, with a ceiling that never under-prices) holds everywhere we have measured it. But some queries are genuinely less predictable than others, and some APIs afford less visibility than others. This page states the soft spots plainly, because a pricing tool you can't trust about its own blind spots isn't worth trusting about prices. In every case below, the designed behavior is **graceful degradation, never refusal**: you always get a contract-valid price plus an honest confidence tag.

## Cyclic-recursion queries

A query that re-enters a type through a list edge (movie → recommendations →
recommendations…; character → episodes → characters…) fans out combinatorially,
and the real backend de-duplicates by an amount only *running* the query reveals.
costQL does not fabricate a dedup guess. It prices the query **structurally** (a
safe max), flags it `confidence: low`, and attaches a caveat: run it once for
the exact cost. On the [TMDB demo](results/tmdb.md), the 4 cyclic held-out queries
averaged ~92% error on the typical estimate, which is exactly why they are
flagged rather than billed on. On [Rick & Morty](results/rickmorty.md), every
loop-shaped query was auto-flagged.

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

## Size curves are light on the single-resolver dimension

The learned size→cost curves currently cover the **batched-loader** dimension
(how a shared read's cost grows with the number of distinct rows in the batch,
the fix measured in the Northwind study). The **single-resolver** size dimension
is less exercised: a declared `limit` on a **leaf compute field** does not scale
the quote yet. On passthrough-style APIs the measured effect of this was ~0%
(list items arrive inside the parent's single fetch), but on a resolver doing
real per-item local work it would matter.

## How sugared queries are priced

`PricingPack.quote()` parses the executable query surface: fields and
arguments, aliases, named and inline fragments, variables, and directives.
Queries never need rewriting before quoting. These aren't limitations, but
they belong on this page because they follow the same design rule as
everything above: where the price can't be exact, it errs high and says so.
What each one means for the price:

- **Fragments and aliases cost nothing extra.** They change how a query is
  written, never what the server does, so a sugared query prices exactly like
  its hand-flattened equivalent. (The same field requested under two aliases
  is priced twice: that *is* extra work.)
- **Polymorphic branches are priced as if every branch fires.** Before a query
  runs, nobody knows which `... on Type` branch each object resolves to, so
  the price walks them all. At most one fires per object, so this is an upper
  bound, and the quote says so in a caveat.
- **Variables** take their values from `quote(query, variables)` (CLI:
  `--variables '{"n": 8}'`) or from the declared default. A variable with
  neither loses its argument, so the ceiling's worst-case bound applies:
  possibly higher than needed, never an under-price.
- **Directives are priced as included.** `@skip`/`@include` can only remove
  work at runtime, so ignoring them never under-prices.
- **The tokenizer is lenient**: it accepts the common GraphQL query surface
  rather than enforcing the full spec grammar.

## Non-goals (by design, not omission)

- **No hosted service.** The pricing pack is a static, local file; there is no
  sidecar, pricing endpoint, or extra API call in the quote path. See
  [the architecture](architecture.md).
- **No dollars, no billing.** costQL speaks cost-units only; the consuming app
  owns the single rate that turns cost-units into money.
- **No buyer-facing transparency mechanism.** How much of a quote's breakdown a
  seller shows their customers is the seller's design call, not costQL's.
