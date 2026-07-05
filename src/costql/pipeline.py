"""Phase 4: CI integration: rebuild-on-schema-change keyed to a schema hash,
versioned model artifacts, and drift detection.

The model is keyed to the schema hash (a pure function of the introspected
type graph). On schema change the hash changes, the published artifact is
marked stale, and a rebuild is required. Runtime pricing needs only the
artifact + introspection (no live backend).
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from .introspect import TypeGraph
from .pricer import CostModel


@dataclass
class Artifact:
    schema_hash: str
    model_version: str        # content hash of the model (topology + costs)
    model: dict               # serialized CostModel

    def to_json(self) -> str:
        return json.dumps({"schema_hash": self.schema_hash,
                           "model_version": self.model_version,
                           "model": self.model}, indent=2, sort_keys=True)

    @staticmethod
    def from_json(s: str) -> Artifact:
        d = json.loads(s)
        return Artifact(d["schema_hash"], d["model_version"], d["model"])


def topology(tg: TypeGraph) -> dict:
    """Schema-derived, measurement-free structure: resolvers, fanout edges,
    fragment branches. Deterministic from introspection alone."""
    return {
        "schema_hash": tg.schema_hash(),
        "resolvers": sorted(r.resolver_id for r in tg.all_resolvers()),
        "fanout_edges": sorted(e.resolver_id for e in tg.fanout_edges()),
        "fragment_branches": sorted(f"{a}:{b}" for a, b in tg.fragment_branches()),
        "root_fields": sorted(f.name for f in tg.root_fields()),
    }


def model_version(model: CostModel) -> str:
    return hashlib.sha256(model.to_json().encode()).hexdigest()[:12]


def publish(tg: TypeGraph, model: CostModel, path: str) -> Artifact:
    art = Artifact(schema_hash=tg.schema_hash(),
                   model_version=model_version(model),
                   model=json.loads(model.to_json()))
    with open(path, "w") as fh:
        fh.write(art.to_json())
    return art


def load_artifact(path: str) -> Artifact:
    with open(path) as fh:
        return Artifact.from_json(fh.read())


def is_stale(art: Artifact, live_tg: TypeGraph) -> bool:
    """Drift detection: artifact's schema hash vs. the live schema hash."""
    return art.schema_hash != live_tg.schema_hash()


def price_offline(art: Artifact, tg: TypeGraph, root_selections, mode="ceiling"):
    """Runtime path: price a query using ONLY the artifact + introspection.
    No harness, no backend, no measurement: pure static traversal."""
    from .pricer import Pricer
    if is_stale(art, tg):
        raise RuntimeError(f"stale artifact: schema_hash {art.schema_hash} != live "
                           f"{tg.schema_hash()}: rebuild required")
    model = CostModel(**art.model)
    return Pricer(tg, model).price(root_selections, mode=mode)
