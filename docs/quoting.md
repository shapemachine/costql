---
description: "What quote() accepts and what the price means: fragments, aliases, variables, and directives are all parsed, and wherever the price can't be exact it errs high."
---

# Quoting queries

`PricingPack.quote()` takes the query your client actually sends. Nothing
needs rewriting before quoting: the parser covers the executable query
surface — fields and arguments, aliases, named and inline fragments,
variables, and directives.

```python
from costql import PricingPack

pack = PricingPack.demo("tmdb_t3")
pack.quote('{ movie(id:"27205"){ title } }')

# variables travel alongside the query, like in any GraphQL request
pack.quote(
    'query($n: Int!){ movie(id:"27205"){ cast(limit:$n){ person{ name } } } }',
    {"n": 4})
```

Same call in JS (see [the JS package](js.md)), and from the CLI:

    costql quote --demo tmdb_t3 --variables '{"n": 4}' 'query($n: Int!){ movie(id:"27205"){ cast(limit:$n){ person{ name } } } }'

Every result is a [contract v1.0](contract.md) quote. One rule governs
everything below: **where the price can't be exact, it errs high and says
so** — the ceiling stays a ceiling.

## What each construct means for the price

- **Aliases cost nothing.** An alias renames a response key; the server does
  the same work. The aliased field is priced under its schema name. The same
  field requested under *two* aliases is priced twice — that *is* extra work.

- **Fragments cost nothing.** A fragment is a reusable spelling of the same
  selections, so a query written with fragments prices **exactly** like its
  hand-flattened equivalent (this equality is pinned by tests in both
  engines). Unknown fragment names and fragment cycles raise instead of
  mispricing silently.

- **Polymorphic branches price as an upper bound.** Before a query runs,
  nobody knows which `... on Type` branch each object resolves to, so the
  price walks every branch. At most one fires per object; the quote carries a
  caveat naming the branched paths. See
  [limitations](limitations.md#polymorphic-branches-price-as-an-upper-bound).

- **Variables** take their values from the `variables` argument or the
  declared default. A variable with neither loses its argument, so the
  field prices at the ceiling's worst-case bound — possibly higher than
  needed, never an under-price. See
  [limitations](limitations.md#a-missing-variable-value-prices-at-the-worst-case).

- **Directives are priced as included.** `@skip` and `@include` can only
  remove work at runtime, so ignoring them never under-prices.

## Two notes on strictness

- The tokenizer is lenient: it accepts the common GraphQL query surface
  rather than enforcing the full spec grammar.
- A document must contain exactly one operation (plus any number of fragment
  definitions); multiple operations raise.
