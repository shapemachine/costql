# TMDB Passthrough — the costQL T3 demo API

A Strawberry GraphQL passthrough over the **live TMDB REST API** (plus one paid
Anthropic field), built to demonstrate a server emitting all three costQL
fidelities from one instrumentation:

- **DataLoader batching** over TMDB endpoints → real, observable **sharing** (T3)
- `CostTraceExtension` publishes per-request `cost_trace` (per-resolver work-ms,
  per-loader batch/coalesce stats, downstream hosts) into `response.extensions`
- Two deliberately awkward cost dimensions:
  - `Movie.chemistryScore` — local O(n²) compute, zero downstream calls
  - `Movie.aiSummary` — a **paid** Anthropic call (the "unmeasurable per-call
    fee" case; costQL samples it in isolation and the fee is authored, not measured)

## Keys (required)

Copy `.env.example` to `.env.local` and fill in real values, then export them
(the app reads plain environment variables — nothing auto-loads dotfiles):

- `TMDB_ACCESS_TOKEN` (v4 bearer, preferred) or `TMDB_API_KEY` — free at
  https://www.themoviedb.org/settings/api
- `ANTHROPIC_API_KEY` — only needed if a query selects `Movie.aiSummary`

## Run

```bash
python -m venv .venv && .venv/bin/pip install -e '.'
set -a; source .env.local; set +a
.venv/bin/uvicorn app.server:app --port 8000
```

Tier gating: set `COSTQL_TIER=T1|T2|T3` to control how much of the cost trace
the server emits (this is how the tier-fidelity comparison was measured).

## Build a pack against it

From the repo root, with the server up:

```bash
costql build --adapter examples/adapters/tmdb.py:tmdb_config --tier T3 --out tmdb_t3.json
costql quote --pack tmdb_t3.json '{ movie(id:"27205"){ title } }'
```

Note: building at T3 makes a handful of real (paid) Anthropic Haiku calls to
sample `Movie.aiSummary` in isolation — the build prints the call budget first.
