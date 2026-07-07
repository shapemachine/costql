# The output contract (v1.0)

Every price costQL produces (at any tier, on any query, predicted or measured) follows one frozen, stable shape. This is the contract an application budgets against: it is enforced by `costql.contract.validate()` (dependency-free), and anything that fails validation is, by definition, a contract violation. The contract is demonstrated against the real TMDB demo at every tier in [`packs/contract_examples.json`](../packs/contract_examples.json) (last run: **18 examples, 0 violations**).

---

## The one guarantee that never changes

> **`price` is always present, a number `>= 0`, in `currency` cost-units, safe to
> bill on**, at every tier and on every query. costQL never refuses to price.

`price` is the billable number: a conservative **ceiling** for a predicted quote
(it never under-prices) or the **exact total** for a query that ran. Everything
else in the result is *explanatory detail*. An app integrates once against the
core and simply gains richer breakdowns as the API's instrumentation improves.
It never has to change how it reads the number it charges on.

Cost-units only, never dollars: the consuming app owns the single rate that turns
cost-units into money. costQL prices; the app bills.

---

## The basis: predicted (normal) vs measured (only when a query runs)

Almost every result you handle is **`predicted`**: an offline quote computed from
the pack, before the query runs. That is the path your app integrates against, and
the rest of this page is about it.

A **`measured`** result only appears when a query *actually executes*, which is not
part of normal offline quoting. It happens in two places: during the build's own
accuracy grading (running queries for real to score the pack), and when a caller
opts into "run it once for the exact cost" on a low-confidence quote. In
day-to-day pricing you never touch it.

| `basis` | when | `price` means |
|---|---|---|
| `predicted` | before the query runs: a **quote** from the static pricing pack (the normal path) | the **safe max** (a ceiling that never under-prices); `typical_price` is the typical everyday cost |
| `measured` | only when a query actually runs (build grading, or the opt-in exact path) | the **exact** work the run caused; `confidence` is always `exact` |

## Three tiers = how sharply cost could be resolved

A tier is the fidelity the API's instrumentation affords (see
[One currency, three fidelities](tiers.md)). It changes what *detail* the result
carries, **not** whether you get a billable `price`.

| detail section | T1 | T2 | T3 |
|---|:--:|:--:|:--:|
| `price`, `currency`, `confidence`, `tier`, `basis`, `schema_hash`, `caveats` | ✅ | ✅ | ✅ |
| `typical_price` | ✅ | ✅ | ✅ |
| `breakdown`: per-resolver cost | | ✅ | ✅ |
| `sharing`: observed dedup / cache | | | ✅ |
| `external_calls`: named outside hosts + call count | | | ✅ |

- **T1**: a single total only. When that is all the API affords, the total is a
  wall-clock proxy (`currency: wall_time_ms`); work hidden by parallelism/batching
  is not decomposed and tends to **under-count** (see the genre-hub example:
  measured T3 156 ms of work → T1 87 ms of elapsed time).
- **T2**: per-resolver work (`currency: work_ms`), but sharing is *inferred*, so
  no observed `sharing` section. (A measured T2 receipt carries no `breakdown`
  either: the run reported a total without a per-resolver split; a *predicted* T2
  quote does carry one, because the model can decompose.)
- **T3**: per-resolver work **plus** observed `sharing` (which loads coalesced or
  hit cache) **plus** `external_calls` (outside hosts named, with the call count;
  no fee, since costQL can't know what the outside service charges).

The validator enforces the gating: e.g. an observed `sharing` section at T1 or T2
is a contract violation.

---

## Field reference

Core (mandatory, every result):

- `contract_version` (string): `"1.0"`.
- `tier` (string): `"T1" | "T2" | "T3"`.
- `basis` (string): `"predicted" | "measured"`.
- `currency` (string): cost-unit; `"work_ms"` (T2/T3, and measured T1 when
  work-ms is present) or `"wall_time_ms"` (T1 wall proxy).
- `price` (number ≥ 0): the billable number (see the guarantee above).
- `typical_price` (number | null): the typical everyday cost; equals `price` for a
  measured receipt.
- `confidence` (string): `"high" | "medium" | "low"` for a quote (predictability,
  orthogonal to tier; cyclic queries are flagged `low`), `"exact"` when measured.
- `schema_hash` (string): which schema/pack produced this.
- `caveats` (string[]): human-readable notes (may be empty).

Tier-gated detail (optional; present only where the table above allows):

- `breakdown` (object[]): `{ resolver_id, cost, invocations, [list_size] }`.
- `sharing` (object[]): observed sharing. Inner shape differs by basis (honest
  asymmetry): **predicted** `{ loader, folds:[resolver…], counted_once }`;
  **measured** `{ loader, requested, calls, saved, cache_hits, external }`.
- `external_calls` (object[]): outside calls costQL observed. Inner shape differs by
  basis: **predicted** `{ resolver_id, host, calls }`; **measured** `{ host, calls }`.
  `host` is the outside address; `calls` is the count. No fee: costQL can't know what
  the outside service charges, so the consuming app prices these (see
  [External calls](external-calls.md)).

A `predicted` result also echoes `query` (convenience, not part of the core).

---

## Real examples (TMDB demo)

**Predicted · T1**: total only, wall-clock proxy (`{ person(id:"6193"){ filmography{ movie{ title } } } }`):

```json
{
  "contract_version": "1.0", "tier": "T1", "basis": "predicted",
  "currency": "wall_time_ms", "price": 56.16, "typical_price": 50.11,
  "confidence": "high", "schema_hash": "26c786209ec27586", "caveats": []
}
```

**Predicted · T3**: same query, now with per-resolver `breakdown` and observed `sharing`:

```json
{
  "contract_version": "1.0", "tier": "T3", "basis": "predicted",
  "currency": "work_ms", "price": 69.35, "typical_price": 55.18,
  "confidence": "high", "schema_hash": "26c786209ec27586", "caveats": [],
  "breakdown": [
    { "resolver_id": "Query.person",       "cost": 49.62, "invocations": 1, "list_size": 1 },
    { "resolver_id": "Person.filmography", "cost": 0.02,  "invocations": 1, "list_size": 20 },
    { "resolver_id": "Credit.movie",       "cost": 0.29,  "invocations": 1, "list_size": 1 }
  ],
  "sharing": [
    { "loader": "/movie/{id}",                "folds": ["Credit.movie"],       "counted_once": true },
    { "loader": "/person/{id}/movie_credits", "folds": ["Person.filmography"], "counted_once": true }
  ]
}
```

**Predicted · T3 with an outside call** (`{ movie(id:"27205"){ aiSummary } }`): `external_calls` names the host and count; your app prices it:

```json
{
  "contract_version": "1.0", "tier": "T3", "basis": "predicted",
  "currency": "work_ms", "price": 2393.23, "typical_price": 1722.62,
  "confidence": "high", "schema_hash": "26c786209ec27586", "caveats": [],
  "breakdown": [
    { "resolver_id": "Query.movie", "cost": 29.68, "invocations": 1, "list_size": 1 },
    { "resolver_id": "Movie.aiSummary", "cost": 1690.41, "invocations": 1, "list_size": 1 }
  ],
  "external_calls": [
    { "resolver_id": "Movie.aiSummary", "host": "api.anthropic.com", "calls": 1 }
  ]
}
```

**Measured · one run, three fidelities** (`{ movie(id:"27205"){ cast(limit:8){ person{ name } } } }`): the same execution downgraded per tier:

```json
// T3: per-resolver + observed sharing (8 person loads fully coalesced: requested 8 -> 0 calls)
{ "tier": "T3", "basis": "measured", "currency": "work_ms", "price": 41.64, "confidence": "exact",
  "breakdown": [ {"resolver_id":"Movie.cast","cost":41.64,"invocations":1},
                 {"resolver_id":"Credit.person","cost":0.0,"invocations":8} ],
  "sharing": [ {"loader":"/movie/{id}/credits","requested":1,"calls":1,"saved":0,"cache_hits":0,"external":false},
               {"loader":"/person/{id}","requested":8,"calls":0,"saved":8,"cache_hits":0,"external":false} ] }

// T2: request total only (trace carried no per-resolver split)
{ "tier": "T2", "basis": "measured", "currency": "work_ms", "price": 41.64, "confidence": "exact",
  "caveats": ["no per-resolver breakdown in trace -> T2 ..."] }

// T1: wall-clock elapsed proxy
{ "tier": "T1", "basis": "measured", "currency": "wall_time_ms", "price": 48.08, "confidence": "exact",
  "caveats": ["no work-ms in trace -> T1 (wall-clock elapsed proxy) ..."] }
```

Validate any pack's output yourself with `costql validate --pack <pack.json>`, or
programmatically via `from costql.contract import validate`.

---

## Stability

`contract_version` is `"1.0"`. Additive, backward-compatible changes (new optional
fields) keep `"1.0"`. Any change that removes or repurposes a field, or alters the
core guarantee, bumps the major version. Consumers should read the core fields by
name and treat unknown fields as forward-compatible additions.
