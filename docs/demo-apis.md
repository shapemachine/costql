# The three demo APIs and their packs

costQL was proven against three real, unrelated GraphQL APIs: a network passthrough we instrumented (TMDB), a public API we don't own and can't change (Rick & Morty), and a batch-heavy SQLite database we instrumented (Northwind). Each one was onboarded with a single small adapter and zero engine changes, and each ships a committed pricing pack in [`packs/`](../packs) that quotes fully offline. Two of the packs are T3 because we instrumented those demo servers ourselves; Rick & Morty is T1 because it is a black box, exactly [the fidelity ladder](tiers.md) a real adoption follows.

## Rick & Morty: the "try it right now" one (T1, black box)

**What it is:** the live public API at `rickandmortyapi.com/graphql`, not ours,
no keys, no server to run.

**What it demonstrates:** black-box onboarding. A 93-line adapter
([`examples/adapters/rickmorty.py`](../examples/adapters/rickmorty.py)), zero
server changes, ~96% accuracy on held-out queries, with a safe max never under the real
cost. The full story is in [the case study](results/rickmorty.md).

**Committed pack:** [`packs/rickmorty_t1.json`](../packs/rickmorty_t1.json),
tier **T1**, currency **`wall_time_ms`** (the black-box wall-clock proxy).

**Try it (offline, right now):**

```bash
costql quote --pack packs/rickmorty_t1.json '{ character(id:"1"){ name } }'
# -> total-only T1 result, high confidence: price (safe max) + typical_price,
#    no breakdown/sharing sections. That detail needs T2/T3 instrumentation

costql quote --pack packs/rickmorty_t1.json \
  '{ character(id:"1"){ episode{ characters{ name } } } }'
# -> confidence: low. The character<->episode loop is cyclic; the price is a
#    structural ceiling with a "run it for the exact cost" caveat
```

## TMDB: the fully instrumented passthrough (T3)

**What it is:** a Strawberry GraphQL passthrough over the live TMDB REST API
([`examples/demos/tmdb`](../examples/demos/tmdb)), with DataLoader batching and a
`cost_trace` extension emitting all three fidelities from one instrumentation.
Running the server needs free TMDB keys; quoting the committed pack needs nothing.

**What it demonstrates:** the full T3 result (observed sharing), plus two
deliberately awkward cost dimensions: `Movie.chemistryScore` (local O(n²)
compute, zero downstream calls: cost that only work-ms can see) and
`Movie.aiSummary` (an **outside** Anthropic call, whose host costQL observes and
names for the app to price). Accuracy across the tiers (T1 17% / T2 11% /
T3 11%) is in [the case study](results/tmdb.md).

**Committed pack:** [`packs/tmdb_t3.json`](../packs/tmdb_t3.json), tier **T3**,
currency **`work_ms`**.

**Try it:**

```bash
costql quote --pack packs/tmdb_t3.json \
  '{ person(id:"6193"){ filmography{ movie{ title } } } }'
# -> high confidence, per-resolver breakdown, and a "sharing" section showing
#    which loaders fold to a single counted fetch

costql quote --pack packs/tmdb_t3.json '{ movie(id:"27205"){ aiSummary } }'
# -> an "external_calls" entry naming api.anthropic.com + the call count (no fee;
#    your app prices the outside call)

costql quote --pack packs/tmdb_t3.json \
  '{ movie(id:"27205"){ recommendations{ recommendations{ title } } } }'
# -> confidence: low. Cyclic recursion, flagged rather than billed
```

## Northwind: the batch-heavy database (T3, plus T1/T2 packs)

**What it is:** a thin GraphQL passthrough over the real Northwind SQLite
reference database (8 categories, 77 products, 609k order-lines) where every
entity fetch goes through a batching DataLoader that coalesces a tick's keys into
one `SELECT … WHERE id IN (…)`
([`examples/demos/northwind`](../examples/demos/northwind)). Running the server
needs a one-command **24 MB reference-DB download**; the committed packs quote
offline without it.

**What it demonstrates:** heavy entity sharing, a coalescing factor of **38.5×**
(963 reads asked → 25 SQL queries), which is exactly where T3's observed sharing
and learned **batch-size curves** earn their keep over T2
([the case study](results/northwind.md)). Packs are committed at all three tiers
so you can quote the *same* query at each fidelity and watch the detail sections
appear.

**Committed packs:**
[`packs/northwind_t1.json`](../packs/northwind_t1.json) (T1, `wall_time_ms`),
[`packs/northwind_t2.json`](../packs/northwind_t2.json) (T2, `work_ms`),
[`packs/northwind_t3.json`](../packs/northwind_t3.json) (T3, `work_ms`).

**Try it:**

```bash
costql quote --pack packs/northwind_t3.json \
  '{ order(id:"15000"){ details(first:15){ product{ category{ name } } } } }'
# -> high confidence (sizes declared); T3 prices the batched product/category
#    reads on their learned size curves instead of charging every repeat

costql quote --pack packs/northwind_t2.json \
  '{ order(id:"15000"){ details(first:15){ product{ category{ name } } } } }'
# -> same query at T2: a breakdown, but no observed sharing and no batch
#    curves. On heavy-sharing hub queries that blindness is the measured
#    T2-vs-T3 accuracy gap

costql quote --pack packs/northwind_t3.json \
  '{ customer(id:"BSBEV"){ orders{ details{ product{ name } } } } }'
# -> confidence: low. Two un-paginated list edges compound on one path, so the
#    size is data-dependent; the ceiling stays safe, the caveat says
#    "declare sizes or run it"
```

## Every pack, one contract

All five packs produce results in the same frozen shape. Validate any of them
with `costql validate --pack <path>`, and see
[the output contract](contract.md) for what each tier's result carries. Quoting
works in Python (`PricingPack.load(path).quote(query)`) or JavaScript (the
`costql` npm package, quote-side).
