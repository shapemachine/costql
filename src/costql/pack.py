"""The pricing pack — a self-contained, static, per-schema pricing reference.

This is the artifact that crosses the build -> use boundary. Everything needed to
price a query lives inside ONE file: the introspected schema, the fitted cost
model (unit costs, sizes, sharing groups, safety), and the hand-authored fee
adjustments. Loading it needs no server, no network, no measurement, and no
re-introspection — a consumer prices queries by pure local traversal.

Design intent (decided with the user): the whole point of a static per-schema
reference is that it is consumed WITHOUT a service. No sidecar, no pricing
endpoint, no extra API call — those would defeat the reason to build a static
reference. A pack is a plain file you can vendor into an app, or even embed in
the docs of whatever tool is pricing the API, and price against locally.

    pack = PricingPack.load("pricing_pack_tmdb.json")
    quote = pack.quote('{ movie(id:"27205"){ cast(limit:8){ person{ name } } } }')
    quote["price"]        # safe billable ceiling, in cost-units (work-ms here)
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field

from .confidence import assess
from .contract import predicted_result
from .introspect import TypeGraph
from .pricer import DOC_FEATURE, CostModel, Pricer
from .query_ir import parse_query

PACK_VERSION = 1


class PackVersionError(ValueError):
    """The file is not a readable costQL pricing pack: not a pack at all,
    missing required sections, or written by a newer costql than this one."""


_REQUIRED_KEYS = ("schema_hash", "introspection", "model")


@dataclass
class PricingPack:
    schema_hash: str
    currency: str
    introspection: dict          # the raw GraphQL introspection (rebuilds the schema)
    model: CostModel             # fitted cost model
    adjustments: dict = field(default_factory=dict)   # #6 authored fees (cost-units)
    tier: str = "T3"             # fidelity the pack was built at (what the API affords)

    # ---- persistence ------------------------------------------------------
    def to_dict(self) -> dict:
        return {
            "pack_version": PACK_VERSION,
            "schema_hash": self.schema_hash,
            "currency": self.currency,
            "tier": self.tier,
            "note": ("Self-contained static pricing reference. Prices queries "
                     "locally with no server/network/measurement. Cost-units only "
                     "(never dollars) — the consuming app converts to money."),
            "introspection": self.introspection,
            "model": json.loads(self.model.to_json()),
            "adjustments": self.adjustments,
        }

    def save(self, path: str) -> None:
        with open(path, "w") as fh:
            json.dump(self.to_dict(), fh, indent=2, sort_keys=True)

    @classmethod
    def load(cls, path: str) -> PricingPack:
        with open(path) as fh:
            d = json.load(fh)
        return cls.from_dict(d)

    @classmethod
    def from_dict(cls, d: dict) -> PricingPack:
        if not isinstance(d, dict) or "pack_version" not in d:
            raise PackVersionError(
                "not a costQL pricing pack: missing 'pack_version'")
        try:
            version = int(d["pack_version"])
        except (TypeError, ValueError):
            raise PackVersionError(
                f"unreadable pack_version: {d['pack_version']!r}") from None
        if version > PACK_VERSION:
            raise PackVersionError(
                f"pack_version {version} was written by a newer costql "
                f"(this one reads <= {PACK_VERSION}) — upgrade costql")
        missing = [k for k in _REQUIRED_KEYS if k not in d]
        if missing:
            raise PackVersionError(
                f"pricing pack is missing required sections: {', '.join(missing)}")
        model = CostModel(**d["model"])
        return cls(schema_hash=d["schema_hash"], currency=d.get("currency", model.cost_currency),
                   introspection=d["introspection"], model=model,
                   adjustments=d.get("adjustments", {}), tier=d.get("tier", "T3"))

    # ---- the app-side interface ------------------------------------------
    def _effective_model(self) -> CostModel:
        """The model with #6 authored fees folded into per-resolver unit costs:
        (measured_unit_cost + authored_unit_cost) then x invocations at price
        time (DECISIONS #6). Fees are in cost-units; 0 (no-op) by default."""
        fees = {}
        for rid, adj in (self.adjustments.get("adjustments") or {}).items():
            try:
                fee = float(adj.get("added_unit_cost", 0.0) or 0.0)
            except (TypeError, ValueError):
                fee = 0.0
            if fee:
                fees[rid] = fee
        if not fees:
            return self.model
        unit = dict(self.model.unit_cost)
        for rid, fee in fees.items():
            unit[rid] = unit.get(rid, 0.0) + fee
        eff = CostModel(**json.loads(self.model.to_json()))
        eff.unit_cost = unit
        eff.batch_groups = dict(self.model.batch_groups)
        return eff

    def quote(self, query: str) -> dict:
        """Price a query from the pack alone — no server, no measurement.

        Returns a FROZEN contract result (see contract.py): the safe billable
        **ceiling** `price` plus a fair `typical_price`, `confidence`, and — gated
        by the pack's tier — a per-resolver `breakdown`, observed `sharing`, and
        named `external_costs`. Confidence is diagnostic (a "run it for the exact
        cost" hint on cyclic queries); the billable number is the ceiling, which
        never under-prices. `query` is echoed for convenience (not part of the
        contract core).
        """
        tg = TypeGraph(self.introspection)
        model = self._effective_model()
        pricer = Pricer(tg, model)
        sels = parse_query(query)

        ceiling = pricer.price(sels, mode="ceiling", fold=True)
        typical = pricer.price(sels, mode="expectation", fold=True)
        recs = pricer.counter.count(sels, mode="ceiling", size_caps=model.max_size)
        conf = assess(tg, recs, sels[0] if sels else None)
        used = {r.resolver_id for r in recs}

        # per-resolver breakdown (T2/T3): the ceiling's cost lines
        breakdown = [b for b in ceiling.per_resolver if b["resolver_id"] != DOC_FEATURE]
        # observed sharing (T3): which of this query's resolvers fold onto a shared
        # loader, so their repeated work is counted once
        folds: dict[str, list[str]] = {}
        for rid, loader in model.batch_groups.items():
            if rid in used:
                folds.setdefault(loader, []).append(rid)
        sharing = [{"loader": lo, "folds": sorted(rs), "counted_once": True}
                   for lo, rs in sorted(folds.items())]
        # named external/paid costs (T3): #6 adjustments for resolvers this query hits
        adj = self.adjustments.get("adjustments") or {}
        external_costs = [
            {"resolver_id": rid, "host": a.get("downstream_host"),
             "authored_fee": float(a.get("added_unit_cost", 0.0) or 0.0),
             "measured_fee": False}
            for rid, a in adj.items() if rid in used]

        result = predicted_result(
            tier=self.tier, currency=self.currency, schema_hash=self.schema_hash,
            price=ceiling.score, typical_price=typical.score, confidence=conf.level,
            caveats=conf.caveats, breakdown=breakdown, sharing=sharing,
            external_costs=external_costs)
        result["query"] = query
        return result
