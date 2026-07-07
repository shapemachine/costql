"""Cost Model Builder (Phase 1, flat unit costs) + Query Pricer (ceiling mode).

Phase 1 has no size functions: each resolver gets a single flat unit cost,
fit from the instrumented coverage-query runs. Ceiling pricing evaluates every
resolver at worst-case (ceiling) invocation counts and sums.

Fit is a non-negative least squares (projected gradient, numpy-only) of
    measured_op_cost  ≈  Σ_resolver  invocations[resolver] × unit_cost[resolver]
with mild L2 regularisation so fixed request overhead concentrates on the
identifiable root resolvers rather than smearing onto shared leaf fields.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field

import numpy as np

from .fanout import FanoutCounter, TenantLimits
from .introspect import TypeGraph
from .query_ir import Selection, count_fields

DOC_FEATURE = "__doc_fields__"   # per-query parse/validate/serialize cost term


@dataclass
class CostModel:
    schema_hash: str
    cost_currency: str
    unit_cost: dict[str, float]      # resolver_id -> flat cost
    safety: float                    # ceiling multiplier (calibration-derived)
    default_cap: int
    noise_buffer_ms: float = 0.0     # additive measurement-noise allowance (ms)
    batch_groups: dict[str, str] = field(default_factory=dict)  # resolver -> shared loader
    loader_fns: dict = field(default_factory=dict)   # loader_id -> size->cost curve dict (batchmodel)
    size_fns: dict = field(default_factory=dict)             # root -> SizeFn (Phase 3)
    max_size: dict[str, int] = field(default_factory=dict)   # observed worst-case list sizes
    typical_size: dict[str, int] = field(default_factory=dict)  # observed AVERAGE list sizes
    uncovered_edges: list[str] = field(default_factory=list)
    scan_before_paginate: list[str] = field(default_factory=list)
    external_hosts: dict[str, str] = field(default_factory=dict)  # bounded resolver_id -> observed outside host

    def to_json(self) -> str:
        return json.dumps({
            "schema_hash": self.schema_hash, "cost_currency": self.cost_currency,
            "unit_cost": self.unit_cost, "safety": self.safety,
            "default_cap": self.default_cap, "max_size": self.max_size,
            "typical_size": self.typical_size,
            "noise_buffer_ms": self.noise_buffer_ms, "batch_groups": self.batch_groups,
            "loader_fns": self.loader_fns,
            "uncovered_edges": self.uncovered_edges,
            "scan_before_paginate": self.scan_before_paginate,
            "external_hosts": self.external_hosts,
        }, indent=2, sort_keys=True)

    @staticmethod
    def from_json(s: str) -> CostModel:
        d = json.loads(s)
        return CostModel(**d)


@dataclass
class QueryCost:
    mode: str
    score: float
    per_resolver: list[dict]
    warnings: list[str] = field(default_factory=list)


def _nnls(A: np.ndarray, b: np.ndarray, ridge: np.ndarray,
          iters: int = 5000) -> np.ndarray:
    """Non-negative least squares via projected gradient with a per-column L2
    ridge. Larger ridge on a column shrinks that resolver's cost toward 0, so a
    heavier ridge on non-root resolvers concentrates fixed/eager work on the
    root resolvers that always fire (conservative for shallow selections)."""
    n = A.shape[1]
    x = np.zeros(n)
    AtA = A.T @ A + np.diag(ridge)
    Atb = A.T @ b
    L = np.linalg.norm(AtA, 2)
    lr = 1.0 / (L + 1e-9)
    for _ in range(iters):
        grad = AtA @ x - Atb
        x = np.maximum(0.0, x - lr * grad)
    return x


class CostModelBuilder:
    def __init__(self, tg: TypeGraph, default_cap: int = 50):
        self.tg = tg
        self.counter = FanoutCounter(tg, default_cap=default_cap)
        self.default_cap = default_cap

    def _is_costed(self, resolver_id: str) -> bool:
        """Only non-leaf resolvers (roots + object/list resolvers) carry cost.
        Scalar leaves are attribute reads -> ~0 measured; excluded from the fit
        so fixed request/DB cost concentrates on the resolvers that do work."""
        parent, _, fname = resolver_id.partition(".")
        obj = self.tg.objects.get(parent)
        if obj is None or fname not in obj.fields:
            return False
        if parent == self.tg.query_type:
            return True   # root resolvers always do work (even scalar-returning)
        return not self.tg.is_leaf(obj.fields[fname].type.base)

    def build(self, calibration: list[tuple[dict, float]],
              uncovered_edges: list[str],
              max_size: dict[str, int] | None = None,
              typical_size: dict[str, int] | None = None,
              noise_buffer_ms: float = 0.0,
              nonroot_ridge: float = 3.0) -> CostModel:
        """calibration: list of (observed_invocations_by_resolver, measured_ms).

        Fits against the OBSERVED operating-point invocation counts (real result
        sizes measured from responses), not worst-case ceiling counts. This lets
        the flat fit recover the true structure (fixed per-root overhead vs.
        marginal per-invocation cost). Ceiling pricing then evaluates the same
        unit costs at worst-case invocation counts.
        """
        max_size = max_size or {}
        typical_size = typical_size or {}

        def costed(r: str) -> bool:
            if r == DOC_FEATURE:      # document-size (parse/serialize) term
                return True
            if not self._is_costed(r):
                return False
            parent = r.partition(".")[0]
            if parent == self.tg.query_type:
                return True   # roots always do DB work, even at 0 rows
            # A non-root resolver that never returned data does ~no work.
            return max_size.get(r, 0) > 0

        resolvers: list[str] = []
        rows = []
        measured = []
        for inv, ms in calibration:
            for r in inv:
                if r not in resolvers and costed(r):
                    resolvers.append(r)
            rows.append(inv)
            measured.append(ms)
        idx = {r: i for i, r in enumerate(resolvers)}
        A = np.zeros((len(rows), len(resolvers)))
        for i, inv in enumerate(rows):
            for r, c in inv.items():
                if r in idx:
                    A[i, idx[r]] = c
        b = np.array(measured, dtype=float)
        # Asymmetric ridge: roots ~free to absorb fixed/eager cost; non-roots
        # penalised so cost concentrates on the always-firing root (safe for
        # shallow selections that omit deep fields whose work is done eagerly).
        def _ridge_for(r: str) -> float:
            if r == DOC_FEATURE:
                return 1.0   # moderate: absorb within-root depth variation only
            if r.partition(".")[0] == self.tg.query_type:
                return 1e-3  # roots free to carry fixed/eager base cost
            return nonroot_ridge
        ridge = np.array([_ridge_for(r) for r in resolvers])
        # Column-normalise so the projected-gradient step conditioning is not
        # dominated by high-magnitude columns (e.g. document field counts ~100
        # vs. invocation counts ~1). Solve in normalised space, then rescale.
        scale = np.maximum(A.max(axis=0), 1e-9)
        x = _nnls(A / scale, b, ridge) / scale
        unit = {r: float(x[idx[r]]) for r in resolvers}

        # Calibration-derived safety: smallest multiplier making every
        # calibration op a valid ceiling. Never peeks at held-out.
        pred = A @ x
        ratios = [m / p for m, p in zip(b, pred) if p > 1e-9]
        safety = max([1.0] + ratios)
        return CostModel(
            schema_hash=self.tg.schema_hash(), cost_currency="wall_time_ms",
            unit_cost=unit, safety=round(safety, 4), default_cap=self.default_cap,
            noise_buffer_ms=round(noise_buffer_ms, 4),
            max_size=max_size, typical_size=typical_size,
            uncovered_edges=uncovered_edges)


class Pricer:
    """Runtime, static-only. Pure traversal of the pre-built model."""

    def __init__(self, tg: TypeGraph, model: CostModel):
        self.tg = tg
        self.model = model
        self.counter = FanoutCounter(tg, default_cap=model.default_cap)
        self._loader_fn_cache = None

    def _loader_fns(self) -> dict:
        """Reconstruct the per-loader size->cost curves (SizeFn) from the model's
        serialized `loader_fns`, once per pricer."""
        if self._loader_fn_cache is None:
            from .batchmodel import fn_from_dict
            self._loader_fn_cache = {lid: fn_from_dict(d)
                                     for lid, d in (self.model.loader_fns or {}).items()}
        return self._loader_fn_cache

    def _size_caps(self, mode: str) -> dict[str, int]:
        """Which observed-size table bounds un-declared-size lists.

        Ceiling (the safe billable upper bound) uses the worst-case observed
        size. Expectation (a fair typical quote) uses the AVERAGE observed size
       : so a request that doesn't declare its size (`first: N`) is priced at
        the typical result, not the largest ever seen. This is the fix for
        worst-case-size over-charging on un-paginated lists (pricing discussion
        Decision 2). Where the size IS declared, both modes honour the declared
        bound, so pagination gives an exact price at every mode.
        """
        if mode == "ceiling":
            return self.model.max_size
        return self.model.typical_size or self.model.max_size

    def price(self, root_selections: list[Selection], mode: str = "ceiling",
              tenant: TenantLimits | None = None, fold: bool = True) -> QueryCost:
        # Phase 3: a size-bearing root is priced by its fitted cost(result_size)
        # instead of the flat per-resolver sum. Ceiling adds safety; expectation
        # is the fitted mean at the requested size.
        if len(root_selections) == 1 and root_selections[0].name in self.model.size_fns:
            from .sizemodel import size_of_query
            root = root_selections[0].name
            fn = self.model.size_fns[root]
            observed = fn.cap if fn.cap is not None else self.model.max_size.get(
                f"{self.tg.query_type}.{root}")
            size = size_of_query(fn, root_selections[0].args, observed_cap=observed, mode=mode)
            base = fn.eval(size)
            warns = ([f"scan_before_paginate:{root}"] if fn.scan_before_paginate else [])
            if mode == "ceiling":
                base = base * fn.safety + fn.buffer
            return QueryCost(mode=mode, score=round(base, 4),
                             per_resolver=[{"resolver_id": root, "size_point": size,
                                            "shape": fn.kind, "cost": round(base, 4)}],
                             warnings=warns)

        records = self.counter.count(root_selections, mode=mode, tenant=tenant,
                                     size_caps=self._size_caps(mode))
        warnings = []
        breakdown = []
        total = 0.0
        # Folding: a resolver backed by a batched loader does one batched query
        # regardless of how many times it fires, so its shared work is counted
        # ONCE per request. fold=False reproduces the naive additive (per-field)
        # model that double-counts shared work.
        folded_batches: set[str] = set()
        # Per-query document-size (parse/validate/serialize) cost.
        doc_unit = self.model.unit_cost.get(DOC_FEATURE, 0.0)
        if doc_unit > 0:
            nfields = count_fields(root_selections)
            dcost = doc_unit * nfields
            total += dcost
            breakdown.append({"resolver_id": DOC_FEATURE, "invocations": nfields,
                              "list_size": 1, "cost": round(dcost, 4)})
        loader_fns = self._loader_fns()
        for rec in records:
            unit = self.model.unit_cost.get(rec.resolver_id, 0.0)
            invs = rec.invocations
            loader = self.model.batch_groups.get(rec.resolver_id)
            if fold and loader is not None:
                if loader in folded_batches:
                    cost = 0.0          # shared work already counted this request
                    invs = 0
                else:
                    folded_batches.add(loader)
                    fn = loader_fns.get(loader)
                    if fn is not None:
                        # SIZE-AWARE fold (DECISIONS #1 x sharing): the batched
                        # loader runs ONE query over the DISTINCT keys this query
                        # touches. The flat `unit` already captures the size-1 batch
                        # cost (fit on isolating ops); the curve adds only the
                        # LEARNED size-dependent MARGINAL for a bigger batch. So a
                        # size-1 fold is identical to the old flat fold (no
                        # regression), while a 40-key batch picks up its real extra
                        # cost. `rec.invocations` upper-bounds the distinct keys;
                        # fn.eval clamps to the loader's observed cardinality (an
                        # 8-row hub caps at 8, so its marginal is ~0).
                        d_keys = rec.invocations
                        marginal = max(0.0, fn.eval(d_keys) - fn.eval(1))
                        cost = unit + marginal   # global model.safety covers the ceiling
                        invs = min(d_keys, fn.cap) if fn.cap else d_keys
                    else:
                        invs = 1        # no curve learned -> flat one-batch fallback
                        cost = unit * invs
            else:
                cost = unit * invs
            total += cost
            if cost > 0:
                breakdown.append({"resolver_id": rec.resolver_id,
                                  "invocations": invs,
                                  "list_size": rec.list_size,
                                  "cost": round(cost, 4)})
        if mode == "ceiling":
            total = total * self.model.safety + self.model.noise_buffer_ms
        return QueryCost(mode=mode, score=round(total, 4),
                         per_resolver=breakdown, warnings=warnings)
