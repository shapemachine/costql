"""Phase 2 — sharing annotations from T3 adapter traces.

Consumes per-operation cost traces (DataLoader batch/cache stats + SQL) captured
by the CostTraceExtension, and builds a resolver dependency graph annotated with:
  * batch groups   — resolvers whose backend work is served by one batched
                     loader call (counted once, not per invocation);
  * cache groups   — repeated keys served from the loader cache;
  * dead loaders   — declared loaders never invoked;
  * un-batched work — repository calls (SQL IN(...)) with no DataLoader,
                     flagged as work disproportionate to a single field.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class LoaderObs:
    loader_id: str
    ops_fired: list[str] = field(default_factory=list)
    batch_calls: int = 0
    batched_keys: int = 0
    requested_keys: int = 0
    cache_hits: int = 0

    @property
    def batched(self) -> bool:
        return self.batch_calls > 0

    @property
    def coalesced(self) -> bool:
        # Requested more than were actually batched -> cache/coalescing happened.
        return self.requested_keys > self.batched_keys


@dataclass
class RepoCall:
    table: str
    max_in: int
    ops: list[str] = field(default_factory=list)


@dataclass
class SharingModel:
    loaders: dict[str, LoaderObs]
    dead_loaders: list[str]
    repo_calls: dict[str, RepoCall]            # un-batched SQL work (non-loader)
    batch_group: dict[str, str]                # resolver_id -> loader_id
    edges: list[tuple[str, str]]               # resolver call graph
    loader_query_table: dict[str, str]         # loader_id -> SQL table it batches


def build_sharing_model(observations: list[dict], tg=None,
                        known_loaders: list[str] | None = None) -> SharingModel:
    """observations: list of {op, trace, invocations} per calibration op.

    trace = response.extensions.cost_trace; invocations = {resolver_id: count}.
    known_loaders: loaders the backend declares (for dead-loader detection);
    ideally emitted by the adapter, supplied by APIConfig otherwise.
    """
    known_loaders = known_loaders or []
    loaders: dict[str, LoaderObs] = {}
    repo: dict[str, RepoCall] = {}
    # Per-loader requested_keys vector (by op) for resolver correlation.
    loader_req_by_op: dict[str, dict[str, int]] = {}
    inv_by_op: dict[str, dict[str, int]] = {}
    loader_tables: dict[str, str] = {}

    for entry in observations:
        op = entry["op"]
        trace = entry["trace"] or {}
        inv_by_op[op] = entry["invocations"]
        for lid, st in (trace.get("loaders") or {}).items():
            lo = loaders.setdefault(lid, LoaderObs(loader_id=lid))
            lo.ops_fired.append(op)
            lo.batch_calls += st.get("batch_calls", 0)
            lo.batched_keys += st.get("batched_keys", 0)
            lo.requested_keys += st.get("requested_keys", 0)
            lo.cache_hits += st.get("cache_hits", 0)
            loader_req_by_op.setdefault(lid, {})[op] = st.get("requested_keys", 0)
        # SQL: a loader's batched query shows as an IN(...) on some table; other
        # IN(...) statements are un-batched repository calls.
        batched_tables = set()
        for lid, st in (trace.get("loaders") or {}).items():
            if st.get("batch_calls", 0) > 0:
                batched_tables.add(lid)  # placeholder; resolved to table below
        for s in (trace.get("sql") or []):
            table = s.get("table")
            inn = s.get("in_count", 0)
            if not table or inn <= 0:
                continue
            # Heuristic: the loader query is the one whose batched_keys matches
            # this IN count on a users-like table; everything else with an IN is
            # an un-batched repository call.
            matched_loader = None
            for lid, st in (trace.get("loaders") or {}).items():
                if st.get("batch_calls", 0) > 0 and inn == max(st.get("batches") or [0]):
                    matched_loader = lid
            if matched_loader:
                loader_tables[matched_loader] = table
            else:
                rc = repo.setdefault(table, RepoCall(table=table, max_in=0))
                rc.max_in = max(rc.max_in, inn)
                if op not in rc.ops:
                    rc.ops.append(op)

    # Dead loaders: declared but never batched anywhere.
    dead = [l for l in known_loaders if l not in loaders or not loaders[l].batched]

    # Map each batched loader to its folded field: the NON-LEAF resolver whose
    # invocation count equals the loader's requested keys (the fanned-out
    # consumer of the loaded entities). That field's backend work is one batched
    # query, so it is folded (counted once, not per invocation).
    def _is_leaf(rid: str) -> bool:
        if tg is None:
            return False
        parent, _, fname = rid.partition(".")
        obj = tg.objects.get(parent)
        return obj is None or fname not in obj.fields or tg.is_leaf(obj.fields[fname].type.base)

    batch_group: dict[str, str] = {}
    for lid, req_by_op in loader_req_by_op.items():
        if not loaders[lid].batched:
            continue
        for op, req in sorted(req_by_op.items(), key=lambda kv: -kv[1]):
            if req <= 0:
                continue
            cand = [rid for rid, cnt in inv_by_op.get(op, {}).items()
                    if cnt == req and not _is_leaf(rid)]
            if cand:
                batch_group[sorted(cand)[0]] = lid
                break

    edges: list[tuple[str, str]] = []
    return SharingModel(loaders=loaders, dead_loaders=dead, repo_calls=repo,
                        batch_group=batch_group, edges=edges,
                        loader_query_table=loader_tables)
