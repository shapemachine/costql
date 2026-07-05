"""Exact post-execution price (HANDOFF principle #6).

When a query has actually **run**, costQL does not predict its cost: it reports
the *exact* work-ms the run caused, read straight from the request's measured
`cost_trace`, decomposed per resolver, with sharing **observed** (not inferred).
This is the T3 truth against which the predictive model (exact.py's sibling,
pricer.py) is later graded.

API-agnostic: consumes only the generic `cost_trace` shape any adapter emits
(`work_ms`, `resolver_work_ms`, `invocations`, `loaders{endpoint: stats}`), plus
a wall-clock fallback for backends that emit no work-ms at all. The tier is
whatever the trace affords, so pricing never refuses (principle #5):

  * resolver_work_ms present            -> T3  (per-resolver work + observed sharing)
  * work_ms present, no decomposition   -> T2  (request work total only)
  * neither (wall-clock only)           -> T1  (elapsed-time proxy, no decomposition)
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ResolverCost:
    resolver_id: str
    work_ms: float
    invocations: int


@dataclass
class LoaderShare:
    """One loader's observed sharing for this request."""
    endpoint: str
    requested_keys: int         # logical loads asked for
    actual_calls: int           # real upstream calls after coalesce + cache
    cache_hits: int             # loads served from the per-request cache
    host: str | None = None
    external: bool = False      # host differs from the API's primary host (#6: paid/3rd-party)

    @property
    def saved(self) -> int:
        """Logical loads that never became an upstream call (coalesced/cached)."""
        return max(0, self.requested_keys - self.actual_calls)


@dataclass
class ExactPrice:
    currency: str               # "work_ms" | "wall_time_ms"
    tier: str                   # "T3" | "T2" | "T1"
    confidence: str             # always exact: the query ran
    total: float                # cost in `currency`
    resolvers: list[ResolverCost] = field(default_factory=list)
    loaders: list[LoaderShare] = field(default_factory=list)
    external_hosts: list[str] = field(default_factory=list)
    caveats: list[str] = field(default_factory=list)

    @classmethod
    def from_cost_trace(cls, ct: dict, wall_ms: float | None = None,
                        primary_host: str | None = None) -> ExactPrice:
        ct = ct or {}
        rwork = ct.get("resolver_work_ms") or {}
        inv = ct.get("invocations") or {}
        work_ms = ct.get("work_ms")
        loaders_raw = ct.get("loaders") or {}

        # --- sharing (T3): one row per loader, observed dedup + cache ----------
        hosts = [lo.get("host") for lo in loaders_raw.values() if lo.get("host")]
        if primary_host is None and hosts:
            primary_host = max(set(hosts), key=hosts.count)   # the dominant backend
        loaders: list[LoaderShare] = []
        external_hosts: list[str] = []
        for ep, lo in sorted(loaders_raw.items()):
            host = lo.get("host")
            external = bool(host) and host != primary_host
            if external and host not in external_hosts:
                external_hosts.append(host)
            loaders.append(LoaderShare(
                endpoint=ep,
                requested_keys=int(lo.get("requested_keys", 0)),
                actual_calls=int(lo.get("batch_calls", 0)),
                cache_hits=int(lo.get("cache_hits", 0)),
                host=host, external=external))

        caveats: list[str] = []
        for h in external_hosts:
            caveats.append(
                f"external/paid host {h}: a per-call fee applies that costQL does "
                f"not measure or price (money is the consuming app's job, DECISIONS "
                f"#6). Billable calls after dedup are the loader's actual_calls; the "
                f"seller authors the per-call fee, counted once per deduped call.")

        # --- tier ladder: use the sharpest resolution the trace affords -------
        if rwork:
            resolvers = [ResolverCost(rid, float(w), int(inv.get(rid, 0)))
                         for rid, w in rwork.items()]
            resolvers.sort(key=lambda r: -r.work_ms)
            return cls(currency="work_ms", tier="T3", confidence="exact (measured)",
                       total=float(work_ms if work_ms is not None
                                   else sum(r.work_ms for r in resolvers)),
                       resolvers=resolvers, loaders=loaders,
                       external_hosts=external_hosts, caveats=caveats)
        if work_ms is not None:
            caveats.append("no per-resolver breakdown in trace -> T2 (request work "
                           "total only); enable T3 tracing for the per-resolver split.")
            return cls(currency="work_ms", tier="T2", confidence="exact (measured)",
                       total=float(work_ms), loaders=loaders,
                       external_hosts=external_hosts, caveats=caveats)
        if wall_ms is not None:
            caveats.append("no work-ms in trace -> T1 (wall-clock elapsed proxy); "
                           "cost hidden by parallelism/batching is not decomposed.")
            return cls(currency="wall_time_ms", tier="T1", confidence="exact (measured)",
                       total=float(wall_ms), caveats=caveats)
        return cls(currency="work_ms", tier="T1", confidence="exact (measured)",
                   total=0.0, caveats=["no cost signal in trace and no wall-clock "
                                       "fallback; ran but unobservable."])

    def render(self, query: str | None = None) -> str:
        u = "ms"
        lines = []
        if query:
            lines.append(f"query: {query.strip()}")
        lines.append(f"EXACT price (post-execution, no model)  ·  tier {self.tier}  ·  "
                     f"{self.confidence}")
        lines.append(f"  total = {self.total:.2f} {u} of work  [{self.currency}]")
        if self.resolvers:
            lines.append("  per-resolver work (observed sharing already folded in):")
            width = max(len(r.resolver_id) for r in self.resolvers)
            for r in self.resolvers:
                share = (r.work_ms / self.total * 100) if self.total else 0.0
                lines.append(f"    {r.resolver_id:<{width}}  {r.work_ms:8.2f} {u}"
                             f"  ({share:4.1f}%)  x{r.invocations} invocations")
        if self.loaders:
            lines.append("  loaders (observed dedup + cache):")
            for lo in self.loaders:
                tag = "  [EXTERNAL/PAID]" if lo.external else ""
                lines.append(
                    f"    {lo.endpoint:<26} requested {lo.requested_keys:>3} -> "
                    f"{lo.actual_calls:>3} upstream calls "
                    f"({lo.saved} saved by sharing, {lo.cache_hits} cache hits)"
                    f"{tag}")
        if self.caveats:
            lines.append("  caveats:")
            for c in self.caveats:
                lines.append(f"    - {c}")
        return "\n".join(lines)
