"""Tracing & tier emission.

One `ResolverTrace` per loader invocation; one `AdapterManifest` per request.
`COSTQL_TIER` selects how much of each trace is emitted — *same code path*, fields
dropped per tier (BUILD §6). That single lever is the whole downgrade-the-signal
calibration experiment (DECISIONS #2).

Tier gating (BUILD §6 table):

  field group                                   | T3 | T2 | T1
  resolver_id/parent/invocation_count/result_sz | ✓ | ✓ | —
  downstream_calls                              | ✓ | ✓ | —
  downstream_latency_ms                         | ✓ per-call | ✓ (aggregate) | —
  local_compute_ms                              | ✓ | ✓ | —
  batch_group/batch_key/cache_hit/cache_key     | ✓ | — | —
  total request wall time                       | ✓ | ✓ | ✓

At T1 no per-resolver traces are emitted at all — only the request wall time.
"""
from __future__ import annotations

import json
import os
import sys
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Optional

# ---- config (env-driven, BUILD §3) -----------------------------------------

VALID_TIERS = ("T1", "T2", "T3")


def _env_tier() -> str:
    t = os.environ.get("COSTQL_TIER", "T3").upper()
    return t if t in VALID_TIERS else "T3"


def _env_sink() -> str:
    return os.environ.get("COSTQL_TRACE_SINK", "./traces.jsonl")


# Fields dropped when downgrading below T3 / T2. Everything else survives to T1's
# gate, where we drop *all* per-resolver traces (only wall time remains).
T3_ONLY_FIELDS = ("batch_group", "batch_key", "cache_hit", "cache_key")


# ---- trace shapes -----------------------------------------------------------


@dataclass
class ResolverTrace:
    resolver_id: str                       # e.g. "Person.filmography"
    parent_resolver_id: Optional[str]      # call-graph edge (None at root)
    parent_type: Optional[str]
    invocation_count: int = 1              # this trace = one loader invocation
    downstream_calls: int = 0              # actual upstream calls AFTER dedup (0|1)
    downstream_latency_ms: Optional[float] = None   # this call's latency (None if none)
    local_compute_ms: float = 0.0          # non-zero only for chemistryScore (§7)
    result_size: Optional[int] = None      # list length where applicable
    downstream_host: Optional[str] = None  # lets costQL auto-flag external/paid (#6)
    batch_group: Optional[str] = None      # = endpoint            (T3 only)
    batch_key: Optional[str] = None        # = id                  (T3 only)
    cache_hit: Optional[bool] = None       # id already loaded this request? (T3 only)
    cache_key: Optional[str] = None        # (T3 only)


@dataclass
class AdapterManifest:
    tier: str
    cost_currency: str = "downstream_calls"
    stack: str = "strawberry + httpx + dataloader"


# ---- tier gating ------------------------------------------------------------


def gate_trace(trace: ResolverTrace, tier: str) -> Optional[dict[str, Any]]:
    """Return the tier-visible dict for one trace, or None if this tier emits no
    per-resolver trace (T1)."""
    if tier == "T1":
        return None
    d = asdict(trace)
    if tier == "T2":
        for f in T3_ONLY_FIELDS:
            d.pop(f, None)
    return d


# ---- per-request tracer -----------------------------------------------------


class RequestTracer:
    """Collects traces for one request, applies tier gating, and writes to the
    JSONL sink (+ stdout echo). Also the single place the AdapterManifest and the
    request wall time are emitted."""

    def __init__(self, tier: Optional[str] = None, sink: Optional[str] = None,
                 echo: bool = True, write_sink: bool = True):
        self.tier = (tier or _env_tier()).upper()
        self.sink = sink if sink is not None else _env_sink()
        self.echo = echo
        self.write_sink = write_sink
        self.manifest = AdapterManifest(tier=self.tier)
        self.traces: list[dict[str, Any]] = []   # tier-gated dicts actually emitted
        self.raw: list[ResolverTrace] = []        # full T3 traces (for in-proc assertions)
        self._t0 = time.perf_counter()
        self.wall_ms: Optional[float] = None
        self._records: list[dict[str, Any]] = [{"type": "manifest", **asdict(self.manifest)}]

    def record(self, trace: ResolverTrace) -> None:
        self.raw.append(trace)
        gated = gate_trace(trace, self.tier)
        if gated is None:
            return
        self.traces.append(gated)
        self._records.append({"type": "trace", **gated})

    def finish(self) -> float:
        """Stamp the request wall time (emitted at every tier) and flush. Idempotent
        so the schema extension and manual test calls don't double-flush."""
        if self.wall_ms is not None:
            return self.wall_ms
        self.wall_ms = (time.perf_counter() - self._t0) * 1000.0
        self._records.append({"type": "wall", "request_wall_ms": self.wall_ms,
                              "tier": self.tier})
        self._flush()
        return self.wall_ms

    def _flush(self) -> None:
        lines = [json.dumps(r) for r in self._records]
        if self.write_sink and self.sink:
            try:
                with open(self.sink, "a") as fh:
                    fh.write("\n".join(lines) + "\n")
            except OSError as e:  # never let tracing crash a request
                print(f"[tracing] sink write failed: {e}", file=sys.stderr)
        if self.echo:
            for line in lines:
                print(line)

    # -- convenience for assertions ------------------------------------------

    def cost_trace(self) -> dict[str, Any]:
        """Aggregate this request's (full-T3) traces into the adapter `cost_trace`
        shape costQL's engine consumes (costql): per-loader batch/coalesce stats
        keyed on endpoint, per-resolver invocation counts, and — the currency
        (DECISIONS #4) — the request's real **work-ms**: summed downstream-call
        durations + local compute. TMDB has no SQL, so `sql` is empty. Always built
        from the full T3 `raw` view — it is the adapter's T3 observation regardless
        of emit tier.

        Work-ms already reflects sharing exactly: a deduped (cache-hit) load has
        `downstream_calls=0` and no latency, so it contributes 0 work-ms. Thus
        `resolver_work_ms` is the *observed*-sharing per-resolver cost, and
        `work_ms` is the request total the engine calibrates/prices against."""
        loaders: dict[str, dict[str, Any]] = {}
        invocations: dict[str, int] = {}
        resolver_work_ms: dict[str, float] = {}
        work_ms = 0.0
        for t in self.raw:
            invocations[t.resolver_id] = invocations.get(t.resolver_id, 0) + t.invocation_count
            w = (t.downstream_latency_ms or 0.0) + (t.local_compute_ms or 0.0)
            resolver_work_ms[t.resolver_id] = resolver_work_ms.get(t.resolver_id, 0.0) + w
            work_ms += w
            bg = t.batch_group
            if not bg:
                continue
            lo = loaders.setdefault(bg, {"batch_calls": 0, "batched_keys": 0,
                                         "requested_keys": 0, "cache_hits": 0,
                                         "work_ms": 0.0, "host": t.downstream_host})
            lo["requested_keys"] += 1               # one logical load
            if t.downstream_calls > 0:              # an actual upstream call (a key fetched)
                lo["batch_calls"] += t.downstream_calls
                lo["batched_keys"] += t.downstream_calls
                lo["work_ms"] += (t.downstream_latency_ms or 0.0)   # this loader's own SQL/HTTP work
            if t.cache_hit:
                lo["cache_hits"] += 1
        for lo in loaders.values():
            lo["batches"] = [1] * lo["batch_calls"]  # TMDB fetches one id per call
            lo["batch_sizes"] = [1] * lo["batch_calls"]
            lo["work_ms"] = round(lo["work_ms"], 4)
        return {"loaders": loaders, "sql": [], "invocations": invocations,
                "work_ms": work_ms, "resolver_work_ms": resolver_work_ms}

    def traces_for(self, resolver_id: str) -> list[dict[str, Any]]:
        return [t for t in self.traces if t.get("resolver_id") == resolver_id]

    def raw_for(self, resolver_id: str) -> list[ResolverTrace]:
        return [t for t in self.raw if t.resolver_id == resolver_id]

    def downstream_calls_for_group(self, batch_group: str) -> int:
        """Total *actual* upstream calls for an endpoint (uses raw T3 view so it
        works regardless of the emitting tier)."""
        return sum(t.downstream_calls for t in self.raw if t.batch_group == batch_group)
