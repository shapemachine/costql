# Examples

## `adapters/`: the three reference adapters

An adapter is the one file you write to onboard an API (~90–150 lines): an
`APIConfig` plus an `InputSource` (real ids to query with), an `ArgResolver`
(how arg names map to those ids), and curated calibration shapes. The engine
never changes.

| Adapter | Target | Fidelity | What it demonstrates |
|---|---|---|---|
| [`rickmorty.py`](adapters/rickmorty.py) | public rickandmortyapi.com (**not ours, can't change it**) | **T1** | the minimal template: black-box onboarding, ~4% mean error |
| [`tmdb.py`](adapters/tmdb.py) | the TMDB demo server (`demos/tmdb`) | **T3** | full trace: observed sharing, local-compute field, paid external field |
| [`northwind.py`](adapters/northwind.py) | the Northwind demo server (`demos/northwind`) | **T3** | heavy DB sharing + per-loader batch-size curves |

Start from `rickmorty.py`. It targets a live public API with no keys, so you
can build a real pack right now:

```bash
pip install 'costql[build]'
costql build --adapter examples/adapters/rickmorty.py:rickmorty_config --out rickmorty.json
costql quote --pack rickmorty.json '{ character(id:"1"){ name } }'
```

## `demos/`: the two instrumented demo servers

Self-hosted GraphQL backends that emit costQL's `cost_trace` extension, the
T2/T3 instrumentation seam. See each demo's README for setup (`demos/tmdb`
needs free TMDB keys; `demos/northwind` needs a one-command 24 MB reference DB
download). They exist so the repo can prove tier fidelity honestly; you don't
need them to use costQL on your own API.
