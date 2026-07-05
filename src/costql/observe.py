"""Response observation — walk a coverage response alongside its selection and
the schema to record, per edge (resolver_id), the result sizes actually
returned. Data-driven: tells us which fanout edges truly fired with >=1 item
(vs. fired-but-empty due to missing data), and supplies observed result sizes.
"""
from __future__ import annotations

from typing import Any

from .introspect import TypeGraph
from .query_ir import Selection


def observe_sizes(tg: TypeGraph, root_selections: list[Selection],
                  data: dict) -> dict[str, list[int]]:
    """Return resolver_id -> list of observed result sizes (list lengths, or
    1 for a present single object, 0 for null)."""
    sizes: dict[str, list[int]] = {}

    def rec(sel: Selection, parent_type: str, value: Any):
        obj = tg.objects.get(parent_type)
        if obj is None:
            return
        f = obj.fields.get(sel.name)
        if f is None:
            return
        rid = f.resolver_id
        if isinstance(value, list):
            sizes.setdefault(rid, []).append(len(value))
            items = value
        elif value is None:
            sizes.setdefault(rid, []).append(0)
            items = []
        else:
            sizes.setdefault(rid, []).append(1)
            items = [value]
        if tg.is_leaf(f.type.base):
            return
        for item in items:
            if isinstance(item, dict):
                for c in sel.children:
                    if c.name in item:
                        rec(c, f.type.base, item[c.name])

    for sel in root_selections:
        if sel.name in data:
            rec(sel, tg.query_type, data[sel.name])
    return sizes


def fired_with_data(sizes: dict[str, list[int]]) -> set[str]:
    return {rid for rid, ss in sizes.items() if any(s >= 1 for s in ss)}
