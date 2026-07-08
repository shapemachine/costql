"""Fanout-based invocation counter.

Static analysis of a query IR: walks the selection tree alongside the schema
and multiplies pagination bounds down the tree to get each resolver's
invocation count. Pure query-visible computation: no measurement.
"""
from __future__ import annotations

from dataclasses import dataclass

from .introspect import Field, TypeGraph
from .query_ir import Selection


@dataclass
class InvocationRecord:
    resolver_id: str
    field_path: str
    invocations: int      # times this resolver's resolver fires
    list_size: int        # items it yields (1 for non-list); feeds children
    is_leaf: bool


@dataclass
class TenantLimits:
    max_first: int | None = None       # caps every paginated field
    default_cap: int = 50                 # unbounded lists (ceiling)
    depth_cap: int | None = None


class FanoutCounter:
    def __init__(self, tg: TypeGraph, default_cap: int = 50):
        self.tg = tg
        self.default_cap = default_cap

    def _pagination_value(self, f: Field, args: dict, mode: str,
                          tenant: TenantLimits | None) -> int | None:
        """The bound implied by this field's own pagination arg, if any."""
        pag = self.tg.pagination_arg(f)
        if pag is None:
            return None
        requested = args.get(pag.name)
        tenant_max = tenant.max_first if tenant else None
        schema_max = 100 if pag.name in ("limit", "first", "last") else self.default_cap
        if mode == "ceiling":
            candidates = [c for c in (requested, tenant_max, schema_max) if c is not None]
            return int(min(candidates)) if candidates else None
        return int(requested) if requested is not None else None

    def _list_size(self, f: Field, args: dict, mode: str, tenant: TenantLimits | None,
                   inherited: list[int], size_caps: dict[str, int]) -> int:
        """Item count a LIST field yields (ceiling = worst case).

        Bounded by the min of every applicable bound: the field's own
        pagination arg, a bound inherited from an arg-bearing ancestor
        (paginated-wrapper pattern), and the largest result size ever OBSERVED
        for this resolver (a resolver cannot return more rows than exist in the
        data). Falls back to the configured default cap only when the resolver
        was never observed.
        """
        default_cap = tenant.default_cap if tenant else self.default_cap
        own = self._pagination_value(f, args, mode, tenant)
        observed = size_caps.get(f.resolver_id)
        candidates = [c for c in (own,
                                  inherited[-1] if inherited else None,
                                  observed) if c is not None]
        if candidates:
            return int(min(candidates))
        return default_cap

    def count(self, root_selections: list[Selection], mode: str = "ceiling",
              tenant: TenantLimits | None = None,
              size_caps: dict[str, int] | None = None) -> list[InvocationRecord]:
        records: list[InvocationRecord] = []
        size_caps = size_caps or {}

        def walk(sel: Selection, parent_type: str, parent_invocations: int,
                 path: str, inherited: list[int]):
            if sel.on is not None:
                # Fragment node: no resolver of its own. Children resolve
                # against the type condition; when that is a branch of an
                # abstract parent, every branch is walked (at most one fires
                # per object, so the sum is a safe upper bound: pack.quote
                # attaches a caveat).
                for c in sel.children:
                    walk(c, sel.on, parent_invocations, f"{path}.{c.name}", inherited)
                return
            obj = self.tg.objects.get(parent_type)
            if obj is None:
                return
            f = obj.fields.get(sel.name)
            if f is None:
                return
            base = f.type.base
            is_leaf = self.tg.is_leaf(base)
            child_inherited = list(inherited)
            if f.type.is_list:
                size = self._list_size(f, sel.args, mode, tenant, inherited, size_caps)
                # This list consumed the nearest inherited bound.
                if self._pagination_value(f, sel.args, mode, tenant) is None and child_inherited:
                    child_inherited.pop()
            else:
                size = 1
                # A non-list field carrying a pagination arg bounds a nested list.
                pv = self._pagination_value(f, sel.args, mode, tenant)
                if pv is not None:
                    child_inherited.append(pv)
            records.append(InvocationRecord(
                resolver_id=f.resolver_id, field_path=path,
                invocations=parent_invocations, list_size=size, is_leaf=is_leaf))
            child_invocations = parent_invocations * size
            for c in sel.children:
                walk(c, base, child_invocations, f"{path}.{c.name}", child_inherited)

        for sel in root_selections:
            walk(sel, self.tg.query_type, 1, sel.name, [])
        return records


def invocations_by_resolver(records: list[InvocationRecord]) -> dict[str, int]:
    out: dict[str, int] = {}
    for r in records:
        out[r.resolver_id] = out.get(r.resolver_id, 0) + r.invocations
    return out
