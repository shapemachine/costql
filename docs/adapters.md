# Writing an adapter

An adapter is the one file you write to onboard an API, proven at ~90–150
lines across three unrelated APIs, with zero engine changes. It tells costQL
what introspection cannot: where the API is, what *real* inputs to query with,
how argument names map to those inputs, and which query shapes to calibrate on.
This page walks through every part, using
[`examples/adapters/rickmorty.py`](https://github.com/shapemachine/costql/blob/main/examples/adapters/rickmorty.py)
(the minimal T1 template) as the running example.

Two shortcuts before writing anything by hand. `costql probe <url>` checks
the endpoint first and reports the tier and `cost_currency` to declare, plus
where real IDs can be harvested. And the whole job delegates well:
[agent-assisted onboarding](agents.md) covers handing this page to a coding
agent.

## The shape of an adapter

An adapter module exports a factory returning an `APIConfig`. Every field is
explained on this page; the comments say where:

```python
from costql.config import APIConfig, MinedInputs, UNSET

def my_config(tier: str = "T1") -> APIConfig:
    return APIConfig(
        name="myapi",                    # labels the pack; default output is pricing_pack_<name>.json
        graphql_url="https://api.example.com/graphql",   # the live endpoint to calibrate
        input_source=MyInputSource(),    # section 1: the real IDs calibration queries with
        arg_resolver=MyArgResolver(),    # section 2: how argument names map to those IDs
        tokens={}, root_auth={}, field_auth={},    # section 4: auth; all empty for public APIs
        uncoverable_fields={}, bounded_fields={},  # section 4: skip / sample-in-isolation lists
        known_loaders=[], size_roots=SIZE_ROOTS,   # section 4: declared loaders + list-size bounds
        default_cap=20,                  # section 4: page size for lists that declare nothing
        cost_currency="wall_time_ms",    # section 4: copy from `costql probe`, never a guess
        tier=tier,                       # section 4: likewise; build downgrades over-claims
        calibration_queries=calibration_queries,   # section 3: the shapes the model is fit on
    )
```

You point the CLI at it: `costql build --adapter my_api.py:my_config`. The
factory's `tier` parameter exists so `costql build --tier T1` can request a
lower fidelity than the adapter's default without editing the file.

## 1. `InputSource`: real inputs, never fabricated

costQL calibrates by running real queries, so it needs real, stable IDs.
`mine()` returns them:

```python
CHARACTERS = {"whale": 1, "small": 100, "heldout": 200}

class MyInputSource:
    def mine(self) -> MinedInputs:
        return MinedInputs(data={"characters": [CHARACTERS]}, tokens={}, uncovered=[])
```

Pick three disjoint entities per collection: **whale** (the most
densely-connected row you know: worst-case fanout), **small** (a light one),
and **heldout** (never used in calibration, so you can evaluate honestly
later). If inputs come from a database, `mine()` is where you query for them.

## 2. `ArgResolver`: how argument names map to inputs

Called for every argument the calibration queries need. Return a literal, or
`UNSET` to let the engine apply generic defaults:

```python
class MyArgResolver:
    def value(self, field_path, arg_name, arg_type_base, mined,
              size="whale", variant="calib"):
        key = "heldout" if variant == "heldout" else size
        if arg_name == "id":
            return str(CHARACTERS.get(key, CHARACTERS["whale"]))
        if arg_name == "page":
            return 1
        return UNSET
```

`field_path` (e.g. `"character.episode"`) lets one resolver serve many types.
Match substrings to route `id` to the right entity table.

## 3. `calibration_queries`: the shapes the model is fit on

The single most important quality lever. Supply a callable
`(size: "whale" | "small") -> list[str]` returning **clean, predictable,
isolating** query strings:

```python
def calibration_queries(size: str) -> list[str]:
    c = CHARACTERS[size]
    return [
        f'{{ character(id:"{c}"){{ name status species }} }}',
        f'{{ character(id:"{c}"){{ name episode{{ name }} }} }}',
        '{ characters { results { name } } }',
    ]
```

Rules of thumb, learned across three APIs:

- **Each list edge crosses into a different type and does not re-enter.**
  Cyclic shapes (`character{ episode{ characters } }`) corrupt a linear fit.
  They are what costQL *flags* at quote time, not what it calibrates on.
- **Cover every resolver you want priced** at least once, ideally in more than
  one composition.
- **Vary declared sizes** (`first:3` / `first:15` / `first:40`) on batched
  edges so per-loader size→cost curves get ≥3 distinct widths to fit
  (see the [Northwind adapter](https://github.com/shapemachine/costql/blob/main/examples/adapters/northwind.py)'s
  deliberate sweep).
- Without the hook, `costql build` falls back to a shallow generic coverage
  sweep. It works, but curated shapes fit measurably better.

## 4. The remaining knobs

- **`size_roots`**: list-returning fields whose result size an argument
  bounds: `{"cast": {"arg": "limit", "offset": 0, "cap": None}}`. `cap` is a
  server-side clamp (e.g. a fixed 20-item page).
- **`default_cap`**: the ceiling for lists that declare nothing (set it to
  the API's page size).
- **`tokens` / `root_auth` / `field_auth`**: auth, all empty for public APIs.
  `tokens` maps a credential name to its bearer value; `root_auth` maps a
  root field to the credential it needs; `field_auth` does the same per
  resolver (`"Movie.privateNotes": "admin"`) for fields stricter than their
  root.
- **`uncoverable_fields`**: resolvers calibration must skip (with reasons).
- **`bounded_fields`**: resolvers kept *out* of calibration fanout and
  sampled once in isolation, e.g. a paid external call
  (`{"Movie.aiSummary": "paid external call"}`). Their per-call *fee* is
  authored in the adjustments file, never measured.
- **`known_loaders`**: only for T2/T3 servers, the loader IDs your cost-trace
  emits, so dead loaders are detected. Empty for black boxes.
- **`one_root_per_op`** (default `True`): generated coverage queries hit one
  DB-backed root field per operation, for servers that can't run sibling
  roots concurrently on a shared session. Leave it unless you know your
  server handles concurrent sibling roots and you want faster calibration.
- **`cost_currency` / `tier`**: don't choose these; observe them. Run
  `costql probe <url>` and copy what it reports: `"wall_time_ms"` / `"T1"`
  for any API you don't control, `"work_ms"` / `"T2"`-or-`"T3"` only when
  the server actually emits the [cost-trace extension](instrumentation.md).
  `costql build` honestly downgrades to T1 if it observes no trace.

## 5. Build, validate, spot-check

```bash
costql build --adapter my_api.py:my_config --out my_api.json
costql validate --pack my_api.json
costql quote --pack my_api.json '{ ...a query your app actually sends... }'
```

Sanity checks worth a minute: the price of a tiny query should roughly match
one real request's latency; a wider `first:` should never price *lower*; a
deliberately cyclic query should come back `confidence: low`.

## The three worked examples

| Adapter | Lines | Fidelity | What to steal from it |
|---|---|---|---|
| `rickmorty.py` | ~120 | T1 | the minimal template; black-box public API |
| `tmdb.py` | ~150 | T3 | bounded (paid) fields, known loaders, curated shapes |
| `northwind.py` | ~160 | T3 | batch-width sweeps for DB loader curves |
