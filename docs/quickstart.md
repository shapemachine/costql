# Quickstart

costQL turns a live GraphQL API into a **pricing pack** (one self-contained
JSON file) and then quotes any query against that pack fully offline. Build
once in Python; quote forever in Python or JavaScript. This page gets you from
zero to your first quote in about a minute, and to your first *own* pack in
about fifteen.

## 1. Quote a committed demo pack (offline, 60 seconds)

```bash
pip install costql
git clone https://github.com/shapemachine/costql && cd costql

costql quote --pack packs/tmdb_t3.json '{ movie(id:"27205"){ cast(limit:8){ person{ name } } } }'
```

```
pricing pack: schema 26c786209ec27586 · tier T3 · currency work_ms · offline

  query      : { movie(id:"27205"){ cast(limit:8){ person{ name } } } }
  tier/basis : T3 · predicted
  price      : 50.5 work_ms   (safe max, never under real cost)
  typical    : 36.4 work_ms   (typical everyday cost)
  confidence : high
```

No server is running. Nothing touched the network. The pack file carries the
schema, the fitted per-resolver costs, and the authored fees: everything a
quote needs. The same works from Python:

```python
from costql import PricingPack

pack = PricingPack.load("packs/tmdb_t3.json")
quote = pack.quote('{ movie(id:"27205"){ cast(limit:8){ person{ name } } } }')
quote["price"]         # 50.5384 (safe max, in cost-units)
```

…and from JavaScript (`npm install costql`):

```ts
import { PricingPack } from "costql";
const pack = await PricingPack.load("packs/tmdb_t3.json");   // or fromObject(await fetch(...).json())
pack.quote('{ movie(id:"27205"){ title } }').price;
```

The JS package is conformance-tested against the Python engine on every CI
run: same pack, same query, same result. See [the JS package](js.md).

## 2. Build your first pack against a real API

The Rick & Morty public API needs no keys, so you can do this right now:

```bash
pip install 'costql[build]'
costql build --adapter examples/adapters/rickmorty.py:rickmorty_config --out rickmorty.json
costql quote --pack rickmorty.json '{ character(id:"1"){ name episode{ name } } }'
```

`costql build` introspects the endpoint, measures a set of clean calibration
queries against it (this takes a couple of minutes: you calibrate by
measuring), fits the cost model, and writes the pack. From then on, quoting is
offline.

Because the Rick & Morty API is a black box we can't instrument, the pack is
**T1**: wall-clock currency, total-only quotes. That is the designed starting
point for any API you can send queries to, measured at ~96% accuracy on
held-out queries ([case study](results/rickmorty.md)). Richer tiers need
server-side instrumentation; see [tier fidelity](tiers.md).

## 3. Point it at your own API

Two ways in. The fast one: hand the job to a coding agent.
[Agent-assisted onboarding](agents.md) gives it one skill to follow
(`skills/costql-adapter`) and a prompt that works; the agent probes the
endpoint, harvests real IDs, builds, and validates the pack for you.

To write it yourself, an adapter is ~90 lines (where the API is, what real IDs
to query with, and a set of curated calibration queries):

```bash
cp examples/adapters/rickmorty.py my_api.py   # the minimal T1 template
# edit: endpoint, ids, arg resolution, calibration shapes
costql build --adapter my_api.py:rickmorty_config --out my_api.json
```

The [adapter guide](adapters.md) walks through every field. Sanity-check the
result with `costql validate --pack my_api.json` and a few spot quotes.

## 4. Ship the pack

The pack is a plain file. Vendor it into the app that needs to budget queries;
load it with either language's `PricingPack`; call `quote()` before running a
query. Every result follows the [frozen output contract](contract.md): `price`
is always present, always a number, always a safe max; cost-units only,
never dollars (converting to money is your app's business).

Cyclic queries (a type re-entering itself through a list edge) are priced as a
structural ceiling and flagged `confidence: low`, never silently billed. See
[limitations](limitations.md) for the full honest list.
