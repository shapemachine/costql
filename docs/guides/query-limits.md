---
description: "Query-complexity limits, demand control, and DoS protection all need a cost per query to threshold on. costQL measures that cost instead of making you hand-author per-field weights."
---

# Limiting expensive queries

Query-complexity limits, demand control, cost-based rate limiting, DoS
protection: every guard against expensive GraphQL queries needs the same thing,
a cost per query to threshold on. Today you hand-author that cost with `@cost`
directives or per-field weights and hope the numbers are right. costQL measures
it instead.

## Same mechanism, measured weights

The mechanism here is mature and well-trodden: score the query statically before
it runs, bound the pagination, reject anything over budget before a resolver
fires. `graphql-query-complexity`, `graphql-cost-analysis`, GraphQL Armor,
Apollo Router demand control, HotChocolate's `MaxFieldCost` — they agree on all
of that. costQL agrees too. They also all leave one thing to you: the cost of
each field, declared by hand. That declaration is a guess, and it is the only
part costQL replaces — with a number timed from your real API.

## Write the gate; costQL supplies the number

costQL prices; it does not block. You keep the gate, a one-liner, and feed it a
measured price:

```python
from costql import PricingPack

pack = PricingPack.load("packs/your_pack.json")

def admit(query, variables=None, budget=250):
    quote = pack.quote(query, variables)
    if quote["price"] > budget:          # safe max: never below the real cost
        raise TooExpensive(quote["price"], budget)
    return quote
```

`price` is the safe ceiling — [guaranteed never below what the query actually
costs](../contract.md) — so a query that clears the budget really is within it.
Bill on that number or block on it; it is the same measured number either way.
The same call works [in JavaScript](../js.md) and [from the CLI](../quoting.md),
so the check can live in a gateway, a resolver, a CI step, or an agent's
planning loop.

## Why measured beats hand-authored, right here

Two query shapes are where hand-authored weights fail, and they are the two a
limit exists to catch:

- **Batching over-blocks the good resolvers.** A field behind a DataLoader
  resolves a list of 100 in one round-trip, not 100. Per-field-times-count math
  scores it at 100× and rejects exactly the well-built resolver you want people
  to use. costQL prices shared work once; on our share-heaviest test that cut
  error from 315% to 12% (see the [Northwind case study](../results/northwind.md)).
- **Recursion slips under-blocked.** A cyclic query is the classic GraphQL DoS
  vector, and a static hand-authored weight never sees it coming — the usual
  fix is a penalty bolted on after an incident. costQL detects cycles up front,
  flags them `confidence: low`, and prices them at a structural ceiling rather
  than a too-low guess (auto-flagged unprompted on the
  [Rick & Morty API](../results/rickmorty.md)).

A number tuned to what the server actually does is the difference between a
limit that holds and one that punishes your best resolvers while waving the
dangerous ones through.

## Your first pack needs nothing from the server

You do not have to instrument anything to start. **T1** prices any GraphQL
endpoint black-box, from the outside, and still returns a safe ceiling — enough
to threshold on today. If your API batches or caches heavily and you want the
limit to stop over-blocking those resolvers, [instrument for T2/T3](../instrumentation.md);
until then T1 errs high, which is the safe direction for a guard. See
[tier fidelity](../tiers.md) for what each tier buys you.
