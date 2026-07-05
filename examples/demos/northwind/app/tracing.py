"""Per-request cost tracing for the Northwind passthrough.

Emits the exact `cost_trace` shape costQL's engine consumes (costql/harness.py
reads it from `response.extensions.cost_trace`):

    {
      "loaders": { table: {requested_keys, batch_calls, batched_keys, cache_hits,
                           host: "local-sqlite", batches: [in_count, ...]} },
      "sql":      [ {table, in_count}, ... ],        # one per real SELECT ... IN (...)
      "invocations":      { resolver_id: count },
      "work_ms":          <summed real SQL execution ms + local compute>,   # the currency (#4)
      "resolver_work_ms": { resolver_id: work_ms },
    }

The sharing signal (DECISIONS #2/#3) lives in the per-loader gap between
`requested_keys` (every logical `.load`) and `batch_calls`/`batched_keys` (the
real `SELECT ... WHERE id IN (...)` calls after coalescing + per-request cache).
Under heavy sharing (e.g. hundreds of order-lines whose products all point at
the same ~8 categories), `requested_keys >> batch_calls`.

`work_ms` already reflects sharing exactly: a coalesced/cached load triggers no
SELECT, so it adds 0 ms. That makes `work_ms` the T3 (observed-sharing) request
cost the engine calibrates and prices against.
"""
from __future__ import annotations

import time
from typing import Any, Optional


class RequestTracer:
    """Collects one request's loader stats, resolver invocations, and real SQL
    timings, then aggregates them into the engine's `cost_trace`."""

    def __init__(self) -> None:
        self.loaders: dict[str, dict[str, Any]] = {}
        self.invocations: dict[str, int] = {}
        self.resolver_work_ms: dict[str, float] = {}
        # per-loader work-ms and per-(loader, resolver) request counts, for
        # attributing a loader's summed SQL time back onto the resolvers that
        # drove it (so resolver_work_ms sums to work_ms).
        self._loader_work: dict[str, float] = {}
        self._loader_resolver_reqs: dict[str, dict[str, int]] = {}
        self.work_ms: float = 0.0
        self._t0 = time.perf_counter()
        self.wall_ms: Optional[float] = None

    def _stats(self, table: str) -> dict[str, Any]:
        return self.loaders.setdefault(table, {
            "requested_keys": 0, "batch_calls": 0, "batched_keys": 0,
            "cache_hits": 0, "host": "local-sqlite", "batches": []})

    # -- called by loaders --------------------------------------------------
    def record_request(self, table: str, resolver_id: str, cache_hit: bool) -> None:
        """One logical `.load(key)`: a requested key. `cache_hit` when the key
        was already materialized this request (coalesced/cached, no SELECT)."""
        st = self._stats(table)
        st["requested_keys"] += 1
        if cache_hit:
            st["cache_hits"] += 1
        r = self._loader_resolver_reqs.setdefault(table, {})
        r[resolver_id] = r.get(resolver_id, 0) + 1

    def record_batch(self, table: str, n_keys: int, dt_ms: float) -> None:
        """One real `SELECT ... WHERE id IN (n_keys)`: the coalesced upstream call."""
        st = self._stats(table)
        st["batch_calls"] += 1
        st["batched_keys"] += n_keys
        st["batches"].append(n_keys)
        self._loader_work[table] = self._loader_work.get(table, 0.0) + dt_ms
        self.work_ms += dt_ms

    def record_invocation(self, resolver_id: str, n: int = 1,
                          local_ms: float = 0.0) -> None:
        """A resolver fired `n` times; `local_ms` is any non-DB CPU work it did."""
        self.invocations[resolver_id] = self.invocations.get(resolver_id, 0) + n
        if local_ms:
            self.resolver_work_ms[resolver_id] = \
                self.resolver_work_ms.get(resolver_id, 0.0) + local_ms
            self.work_ms += local_ms

    # -- aggregation --------------------------------------------------------
    def cost_trace(self) -> dict[str, Any]:
        rwork = dict(self.resolver_work_ms)
        sql: list[dict[str, Any]] = []
        for table, st in self.loaders.items():
            # attribute this loader's summed SQL time across the resolvers that
            # requested it, weighted by request count (sums back to work_ms).
            reqs = self._loader_resolver_reqs.get(table, {})
            total_req = sum(reqs.values()) or 1
            lw = self._loader_work.get(table, 0.0)
            for rid, cnt in reqs.items():
                rwork[rid] = rwork.get(rid, 0.0) + lw * cnt / total_req
            for n in st["batches"]:
                sql.append({"table": table, "in_count": n})
        # emit each loader's own summed SQL work-ms + per-batch (distinct_keys, work_ms)
        # samples, so costQL can fit a per-loader size->cost curve (DECISIONS #1).
        loaders_out = {}
        for t, st in self.loaders.items():
            d = {k: v for k, v in st.items()}
            d["work_ms"] = round(self._loader_work.get(t, 0.0), 4)
            d["batch_sizes"] = list(st["batches"])                 # distinct keys per SELECT
            loaders_out[t] = d
        return {"loaders": loaders_out, "sql": sql,
                "invocations": dict(self.invocations),
                "work_ms": round(self.work_ms, 4),
                "resolver_work_ms": {k: round(v, 4) for k, v in rwork.items()}}

    def finish(self) -> float:
        if self.wall_ms is None:
            self.wall_ms = (time.perf_counter() - self._t0) * 1000.0
        return self.wall_ms
