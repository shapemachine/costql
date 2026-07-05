"""The API-agnostic boundary.

Everything the framework needs to know about a *specific* GraphQL deployment
that is NOT derivable from introspection lives behind this interface:
endpoint + auth, permissions (not introspectable), input mining (needs dataset
access), the tracing adapter's declared loaders, and size-root caps (runtime
clamps invisible to the schema).

`costql` imports only this module's types; a concrete API is onboarded by
implementing `InputSource` + `ArgResolver` and filling in an `APIConfig`
(see examples/adapters/ for the reference implementations). No file under
`costql/` contains a literal specific to any one API.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


class _Unset:
    """Sentinel: an ArgResolver did not handle this argument."""
    def __repr__(self) -> str:
        return "UNSET"


UNSET = _Unset()


@dataclass
class MinedInputs:
    """Mined dataset inputs, kept generic. ``data`` holds arbitrary per-API
    collections (e.g. {"users": [...], "rooms": [...]}); ``tokens`` maps a
    credential name (matching APIConfig.root_auth/field_auth values) to a bearer
    token; ``uncovered`` lists edges with no mineable input, with reasons."""
    data: dict[str, list[dict]] = field(default_factory=dict)
    tokens: dict[str, str] = field(default_factory=dict)
    uncovered: list[str] = field(default_factory=list)


class InputSource(Protocol):
    """Mines real inputs from a live deployment (DB, API, or fixtures)."""
    def mine(self) -> MinedInputs: ...


class ArgResolver(Protocol):
    """Supplies literal values for a query's arguments. Returns UNSET for
    arguments it does not handle, letting the core apply generic scalar
    defaults. ``variant`` is "calib" or "heldout" so an implementation can pick
    disjoint inputs for the held-out set; ``size`` is "whale" or "small"."""
    def value(self, field_path: str, arg_name: str, arg_type_base: str,
              mined: MinedInputs, size: str = "whale",
              variant: str = "calib") -> Any: ...


@dataclass
class APIConfig:
    """Complete per-API configuration. The only API-aware object the runner
    constructs; the core takes it (or its fields) as parameters."""
    name: str
    graphql_url: str
    input_source: InputSource
    arg_resolver: ArgResolver
    tokens: dict[str, str] = field(default_factory=dict)          # credential -> bearer
    root_auth: dict[str, str] = field(default_factory=dict)       # root field -> credential
    field_auth: dict[str, str] = field(default_factory=dict)      # resolver_id -> credential
    uncoverable_fields: dict[str, str] = field(default_factory=dict)  # resolver_id -> reason
    bounded_fields: dict[str, str] = field(default_factory=dict)   # resolver_id -> reason: coverable
    # but kept OUT of the combinatorial fanout panel and sampled in ISOLATION instead
    # (e.g. a paid external call whose per-invocation cost is a constant — a few samples
    # suffice; fanning it out would multiply cost with no modelling gain). The fee itself
    # is authored via the #6 adjustments file, not measured.
    known_loaders: list[str] = field(default_factory=list)        # declared loaders (dead-loader detection)
    size_roots: dict[str, dict] = field(default_factory=dict)     # root -> {arg, offset, cap}
    metrics_url: str | None = None
    one_root_per_op: bool = True     # server can't run sibling DB roots concurrently
    default_cap: int = 50
    cost_currency: str = "wall_time_ms"
    tier: str = "T1"
    # Optional curated calibration shapes: a callable (size: "whale" | "small") ->
    # list[GraphQL query strings]. RECOMMENDED — fitted unit costs are only as
    # trustworthy as the clean, predictable, isolating shapes they are fit on
    # (deep cyclic shapes corrupt a linear fit; they are what costQL FLAGS, not
    # calibrates against). When absent, build_pack falls back to a conservative
    # shallow coverage sweep. The reference adapters carry worked examples.
    calibration_queries: Any | None = None
    # Optional API-specific held-out fixtures: (tg, mined) -> list[LabeledQuery].
    held_out_extra: Any | None = None
