"""The pricing pack: a self-contained, static, per-schema pricing reference.

This is the artifact that crosses the build -> use boundary. Everything needed to
price a query lives inside ONE file: the introspected schema and the fitted cost
model (unit costs, sizes, sharing groups, safety, and any observed outside hosts).
Loading it needs no server, no network, no measurement, and no re-introspection:
a consumer prices queries by pure local traversal.

Design intent (decided with the user): the whole point of a static per-schema
reference is that it is consumed WITHOUT a service. No sidecar, no pricing
endpoint, no extra API call: those would defeat the reason to build a static
reference. A pack is a plain file you can vendor into an app, or even embed in
the docs of whatever tool is pricing the API, and price against locally.

    pack = PricingPack.load("pricing_pack_tmdb.json")
    quote = pack.quote('{ movie(id:"27205"){ cast(limit:8){ person{ name } } } }')
    quote["price"]        # safe billable ceiling, in cost-units (work-ms here)
"""
from __future__ import annotations

import json
from dataclasses import dataclass

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
                     "(never dollars); the consuming app converts to money."),
            "introspection": self.introspection,
            "model": json.loads(self.model.to_json()),
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
    def demo(cls, name: str = "tmdb_t3") -> PricingPack:
        """Load a demo pricing pack that ships inside the installed package.

        So a bare `pip install costql` can run the quickstart with no repo
        checkout, no file to fetch, and no network. `name` is the demo's short
        name (e.g. "tmdb_t3" -> the bundled demo_tmdb_t3.json). Real packs are
        loaded from your own file with `load(path)`.
        """
        from importlib.resources import files
        res = files("costql").joinpath("data", f"demo_{name}.json")
        try:
            text = res.read_text()
        except (FileNotFoundError, OSError):
            raise FileNotFoundError(f"no bundled demo pack named {name!r}") from None
        return cls.from_dict(json.loads(text))

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
                f"(this one reads <= {PACK_VERSION}); upgrade costql")
        missing = [k for k in _REQUIRED_KEYS if k not in d]
        if missing:
            raise PackVersionError(
                f"pricing pack is missing required sections: {', '.join(missing)}")
        model = CostModel(**d["model"])
        return cls(schema_hash=d["schema_hash"], currency=d.get("currency", model.cost_currency),
                   introspection=d["introspection"], model=model,
                   tier=d.get("tier", "T3"))

    # ---- the app-side interface ------------------------------------------
    def quote(self, query: str) -> dict:
        """Price a query from the pack alone: no server, no measurement.

        Returns a FROZEN contract result (see contract.py): the safe billable
        **ceiling** `price` plus a fair `typical_price`, `confidence`, and: gated
        by the pack's tier: a per-resolver `breakdown`, observed `sharing`, and
        named `external_calls`. Confidence is diagnostic (a "run it for the exact
        cost" hint on cyclic queries); the billable number is the ceiling, which
        never under-prices. `query` is echoed for convenience (not part of the
        contract core).
        """
        tg = TypeGraph(self.introspection)
        model = self.model
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
        # named external calls (T3): outside hosts costQL OBSERVED at build time for
        # the resolvers this query hits, with the ceiling call count. No fee: costQL
        # never knows what the outside service charges; the consuming app prices it.
        inv_by_rid = {b["resolver_id"]: int(b.get("invocations", 1)) for b in breakdown}
        external_calls = [
            {"resolver_id": rid, "host": host, "calls": inv_by_rid.get(rid, 1)}
            for rid, host in sorted(self.model.external_hosts.items()) if rid in used]

        result = predicted_result(
            tier=self.tier, currency=self.currency, schema_hash=self.schema_hash,
            price=ceiling.score, typical_price=typical.score, confidence=conf.level,
            caveats=conf.caveats, breakdown=breakdown, sharing=sharing,
            external_calls=external_calls)
        result["query"] = query
        return result
