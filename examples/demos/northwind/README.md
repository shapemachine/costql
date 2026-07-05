# Northwind Passthrough — costQL heavy-sharing calibration demo

A thin Strawberry GraphQL passthrough over the **real** Northwind SQLite dataset
(the jpwhite3/northwind-SQLite3 reference DB: 8 categories, 77 products, 16k
orders, 609k order-details). Its only job is to be a second, **non-movie,
DB-backed** schema costQL can measure — and one deliberately shaped for **heavy
entity sharing**, so one query re-requests the same few hub entities (only ~8
categories) dozens–hundreds of times and the DataLoaders coalesce them.

Nothing here fabricates rows: every resolver runs a real `SELECT` against
`northwind.db`, and every entity fetch goes through a batching `(table, id)`
DataLoader that coalesces a tick's keys into ONE `SELECT … WHERE id IN (…)` and
caches per request. `CostTraceExtension` publishes the per-request `cost_trace`
(per-loader `requested_keys`/`batch_calls`/`cache_hits`, host `local-sqlite`, and
summed SQL `work_ms`) into `response.extensions` — the seam costQL reads.

## Get the data (gitignored — 24 MB reference DB)

```
curl -sSL -o northwind.db \
  https://raw.githubusercontent.com/jpwhite3/northwind-SQLite3/main/dist/northwind.db
```

## Run

```bash
python -m venv .venv && .venv/bin/pip install -e '.'
.venv/bin/uvicorn app.server:app --port 8010
```

Confirm the coalescing (requested_keys ≫ batch_calls):

```bash
.venv/bin/python probe_sharing.py
```

## Build a pack against it

From the repo root, with the server up:

```bash
costql build --adapter examples/adapters/northwind.py:northwind_config --tier T3 --out northwind_t3.json
costql quote --pack northwind_t3.json '{ order(id:"15000"){ details(first:15){ product{ category{ name } } } } }'
```
