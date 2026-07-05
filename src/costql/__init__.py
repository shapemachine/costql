"""costQL: price GraphQL queries before you run them.

Build side (seller, needs network + ``costql[build]``): point ``build_pack`` at a
live GraphQL endpoint with a small adapter (an :class:`APIConfig`) and it
calibrates a cost model and emits a **pricing pack**: one self-contained JSON
file (schema + fitted per-resolver costs + authored fees).

Quote side (app, fully offline): load the pack and price any query by pure local
traversal: no server, no network, no measurement::

    from costql import PricingPack
    pack = PricingPack.load("tmdb_t3.json")
    quote = pack.quote('{ movie(id:"27205"){ cast(limit:8){ person{ name } } } }')
    quote["price"]        # safe billable ceiling, in cost-units (never dollars)

Every quote is a frozen contract v1.0 result (see :mod:`costql.contract`).

Public surface (the semver compatibility contract): exactly what this module
exports. Submodules remain importable but are internal and may change.
"""
from ._version import __version__
from .build import build_pack
from .config import UNSET, APIConfig, ArgResolver, InputSource, MinedInputs
from .contract import CONTRACT_VERSION, validate
from .pack import PackVersionError, PricingPack

__all__ = [
    "__version__",
    "build_pack",
    "APIConfig",
    "ArgResolver",
    "InputSource",
    "MinedInputs",
    "UNSET",
    "CONTRACT_VERSION",
    "validate",
    "PricingPack",
    "PackVersionError",
]
