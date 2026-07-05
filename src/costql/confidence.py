"""Per-query confidence assessment (fidelity ladder, DECISIONS #5).

Confidence is orthogonal to *tier*: tier says how sharply the backend lets us
*measure* work (T1/T2/T3); confidence says how *predictable* a specific query's
pre-execution price is. The dominant unpredictability on a cyclic schema is
**cyclic recursion**: a query that re-enters a type through a list edge
(`recommendations`→`recommendations`, `filmography`→`movie`→`recommendations`)
fans out combinatorially in the static count, but the real backend dedups the
overlapping entities at runtime by an amount that depends on *which* rows
overlap — unknowable before the query runs (principle #6: real cost only comes
from running). So we don't fabricate a dedup guess; we price the query
structurally and **flag it**, downgrading confidence in proportion to how much
of its predicted cost sits below a cyclic re-entry point.

Pure static analysis over the query IR + schema — no measurement.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .introspect import TypeGraph
from .query_ir import Selection


@dataclass
class Confidence:
    level: str                 # "high" | "medium" | "low"
    score: float               # 1 - cyclic_share, in [0, 1]
    cyclic_share: float        # fraction of predicted invocations below a cyclic re-entry
    cyclic_paths: list[str] = field(default_factory=list)   # where recursion re-enters a type
    caveats: list[str] = field(default_factory=list)


def _band(score: float) -> str:
    if score >= 0.8:
        return "high"
    if score >= 0.5:
        return "medium"
    return "low"


def _data_dependent_paths(tg: TypeGraph, selection: Selection) -> set:
    """Field paths sitting at/below >=2 UN-DECLARED (un-paginated) list edges on a
    single path — a compounding fanout whose size the query never pins, so how many
    items (and how much they de-duplicate downstream) is data-dependent. ONE
    un-declared list is usually fine (bounded, low variance -> stays predictable);
    two or more multiply the uncertainty. This is the un-paginated-size soft spot,
    a different failure mode from cyclic recursion: the ceiling stays a safe upper
    bound, but the typical estimate can drift, so we flag it rather than pretend
    precision. Declaring sizes (pagination) collapses it back to predictable."""
    flagged: set = set()

    def walk(sel: Selection, parent_type: str, path: str, undeclared: int):
        obj = tg.objects.get(parent_type)
        if obj is None:
            return
        f = obj.fields.get(sel.name)
        if f is None:
            return
        u = undeclared
        if f.type.is_list:
            pag = tg.pagination_arg(f)
            declared = pag is not None and pag.name in (sel.args or {})
            if not declared:
                u += 1
        if u >= 2:
            flagged.add(path)
        for c in sel.children:
            walk(c, f.type.base, f"{path}.{c.name}", u)

    walk(selection, tg.query_type, selection.name, 0)
    return flagged


def assess(tg: TypeGraph, records, selection: Selection | None = None) -> Confidence:
    """records: FanoutCounter InvocationRecords for the query. A record sits
    "below a cyclic re-entry" when its field path revisits a base type that
    already appeared earlier on the path via a LIST edge — the signature of
    unbounded combinatorial recursion whose real dedup we can't predict."""
    # Map each record's field_path -> base type + whether the edge is a list.
    total = 0.0
    cyclic = 0.0
    cyclic_paths: list[str] = []
    for r in records:
        total += r.invocations
    # Reconstruct per-path type lineage from the schema by walking field paths.
    # A record is below a cycle when its path RE-ENTERS a base type it already
    # visited, with at least one list edge on the multiplying path — the graph-
    # cycle signature (e.g. Movie→…→Movie via a list), whose runtime dedup of the
    # overlapping set is unknowable pre-execution. Nested lists of DISTINCT types
    # (movie→cast→person) don't re-enter and stay predictable → high confidence.
    for r in records:
        parts = r.field_path.split(".")
        seen_types: set[str] = set()
        list_edges = 0
        cur_type = tg.query_type
        is_below_cycle = False
        for name in parts:
            obj = tg.objects.get(cur_type)
            if obj is None:
                break
            f = obj.fields.get(name)
            if f is None:
                break
            base = f.type.base
            if f.type.is_list:
                list_edges += 1
            # Re-entered a type already on the path, and a list edge multiplies it.
            if base in seen_types and (f.type.is_list or list_edges > 0):
                is_below_cycle = True
            seen_types.add(base)
            cur_type = base
        if is_below_cycle:
            cyclic += r.invocations
            if r.field_path not in cyclic_paths:
                cyclic_paths.append(r.field_path)
    cyclic_share = (cyclic / total) if total > 0 else 0.0
    # Second failure mode: data-dependent size from compounding un-declared lists.
    dd_paths = _data_dependent_paths(tg, selection) if selection is not None else set()
    dd_hit = sorted({r.field_path for r in records if r.field_path in dd_paths})
    cyc_set = set(cyclic_paths)
    # Overall uncertain share = invocations below EITHER failure mode (no double count).
    uncertain = sum(r.invocations for r in records
                    if r.field_path in cyc_set or r.field_path in dd_paths)
    share = (uncertain / total) if total > 0 else 0.0
    score = round(1.0 - share, 3)
    caveats = []
    if cyclic_paths:
        caveats.append(
            f"cyclic recursion re-enters a type through a list edge at "
            f"{len(cyclic_paths)} path(s) ({', '.join(sorted(cyclic_paths)[:3])}"
            f"{'…' if len(cyclic_paths) > 3 else ''}); the static fanout counts the "
            f"worst case, but the real backend dedups overlapping entities by an "
            f"amount that only running the query reveals. Price is a structural "
            f"upper bound, confidence downgraded; run it for the exact T3 cost.")
    if dd_hit:
        caveats.append(
            f"un-declared list sizes compound at {len(dd_hit)} path(s) "
            f"({', '.join(dd_hit[:3])}{'…' if len(dd_hit) > 3 else ''}); how many "
            f"items they yield and de-duplicate to is data-dependent, so the typical "
            f"estimate can drift (the ceiling stays a safe upper bound). Declare "
            f"sizes (pagination) for an exact quote, or run it.")
    return Confidence(level=_band(score), score=score, cyclic_share=round(cyclic_share, 3),
                      cyclic_paths=cyclic_paths, caveats=caveats)
