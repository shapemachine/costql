"""Generic input provider.

The core knows how to fill *pagination* and generic *scalar* arguments; every
identity/domain argument (which arg names map to which mined entity, how a
"whale" size is chosen from the data) is delegated to the API's ArgResolver.
Nothing here is specific to any one API: the mining itself lives behind
config.InputSource (see apis/tmdb.py).
"""
from __future__ import annotations

from typing import Any

from .config import UNSET, APIConfig, MinedInputs


class ArgProvider:
    """Resolves literal argument values for coverage/held-out queries.

    Asks the API's ArgResolver first; falls back to generic scalar defaults for
    anything it declines to handle. ``variant`` ("calib"/"heldout") lets the
    resolver return disjoint inputs for the held-out set.
    """

    def __init__(self, config: APIConfig, mined: MinedInputs, variant: str = "calib"):
        self.config = config
        self.m = mined
        self.variant = variant

    def value(self, field_path: str, arg_name: str, arg_type_base: str,
              size: str = "whale") -> Any:
        v = self.config.arg_resolver.value(field_path, arg_name, arg_type_base,
                                            self.m, size, self.variant)
        if v is not UNSET:
            return v
        # Generic, schema-level fallbacks.
        if arg_type_base == "Int":
            return 10
        if arg_type_base == "Float":
            return 0.0
        if arg_type_base == "Boolean":
            return True
        if arg_type_base == "DateTime":
            return "2000-01-01T00:00:00Z"
        if arg_type_base == "ID":
            return "00000000-0000-0000-0000-000000000000"
        return "x"
