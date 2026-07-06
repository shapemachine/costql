"""Probe a live GraphQL endpoint BEFORE an adapter exists.

One command answers what a human (or a coding agent) needs to know before
writing an adapter:

- is the endpoint reachable and introspectable,
- which fidelity tier it can support **today** (does it emit
  ``extensions.cost_trace``, and how much of it), so ``tier`` and
  ``cost_currency`` come from observation instead of a guess,
- which root fields can supply **real IDs** for calibration without any
  human-curated list (the "id sources": argument-free roots that reach a
  list of id-bearing objects).

This is the first step of agent-assisted onboarding (skills/costql-adapter):
``costql probe <url>`` then write the adapter from what it reports.
"""
from __future__ import annotations

from .introspect import TypeGraph


class ProbeError(RuntimeError):
    """The endpoint could not be probed (unreachable, not GraphQL, no schema)."""


# Args that let an ID harvest iterate for MORE rows. Superset of the engine's
# fanout-bound args (introspect.PAGINATION_ARGS): cursor/offset-style args
# select a slice rather than bound its size, which is all harvesting needs.
_ITERATION_ARGS = ("page", "offset", "after", "skip", "cursor")


def _default_post(url: str, payload: dict, headers: dict, timeout: float) -> dict:
    from .harness import _requests
    requests = _requests()
    r = requests.post(url, json=payload, headers=headers, timeout=timeout)
    r.raise_for_status()
    return r.json()


def _id_sources(tg: TypeGraph) -> list[dict]:
    """Root paths an agent can query to harvest real entity IDs.

    A source is a root field callable WITHOUT arguments (else we would need
    IDs to find IDs) that reaches a list of OBJECTs carrying an ``id`` field:
    either directly (``episodes: [Episode]``) or through one wrapper hop
    (``characters: Characters`` -> ``results: [Character]``, the connection
    pattern). Each source notes its pagination arg when it has one.
    """
    sources: list[dict] = []
    for root in tg.root_fields():
        if any(a.required for a in root.args):
            continue
        page = tg.pagination_arg(root)
        if page is None:
            page = next((root.arg(n) for n in _ITERATION_ARGS if root.arg(n)), None)
        base = root.type.base

        def id_bearing(type_name: str) -> bool:
            obj = tg.objects.get(type_name)
            return bool(obj and obj.kind == "OBJECT" and "id" in obj.fields)

        if root.type.is_list and id_bearing(base):
            sources.append({"path": root.name, "entity": base,
                            "pagination_arg": page.name if page else None})
            continue
        wrapper = tg.objects.get(base)
        if wrapper is None or wrapper.kind != "OBJECT" or root.type.is_list:
            continue
        for sub in wrapper.fields.values():
            if sub.type.is_list and id_bearing(sub.type.base):
                sources.append({"path": f"{root.name}.{sub.name}", "entity": sub.type.base,
                                "pagination_arg": page.name if page else None})
                break
    return sources


def probe_endpoint(url: str, headers: dict | None = None, timeout: float = 30,
                   post=_default_post) -> dict:
    """Probe ``url`` and return a report dict (see ``costql probe --json``)."""
    from .introspect import INTROSPECTION_QUERY
    hdrs = {"Content-Type": "application/json", **(headers or {})}

    try:
        intro = post(url, {"query": INTROSPECTION_QUERY}, hdrs, timeout)
    except Exception as e:  # requests errors, JSON decode, HTTP status
        raise ProbeError(f"endpoint unreachable or not GraphQL: {e}") from e
    if not isinstance(intro, dict) or not (intro.get("data") or {}).get("__schema"):
        err = (intro.get("errors") or [{}])[0].get("message") if isinstance(intro, dict) else None
        raise ProbeError("endpoint answered but introspection returned no schema"
                         + (f" ({err})" if err else "")
                         + "; if introspection is disabled, enable it for the build")

    tg = TypeGraph(intro)

    try:
        resp = post(url, {"query": "{ __typename }"}, hdrs, timeout)
    except Exception as e:
        raise ProbeError(f"introspection worked but a trivial query failed: {e}") from e
    trace = (resp.get("extensions") or {}).get("cost_trace") or {}
    has_work = bool(trace.get("work_ms") is not None or trace.get("resolver_work_ms")
                    or trace.get("invocations"))
    has_loaders = bool(trace.get("loaders"))
    tier = "T3" if (has_loaders and has_work) else ("T2" if has_work else "T1")
    currency = "wall_time_ms" if tier == "T1" else "work_ms"

    notes = []
    if tier == "T1":
        notes.append("no cost_trace observed: if your server gates tracing "
                     "(e.g. behind an env var), enable it and re-probe")
    if has_loaders and not has_work:
        notes.append("trace has loaders but no work_ms: emit work_ms to claim T2/T3")

    return {
        "url": url,
        "schema_hash": tg.schema_hash(),
        "types": len(tg.objects) + len(tg.scalars) + len(tg.enums),
        "root_fields": [f.name for f in tg.root_fields()],
        "trace_observed": sorted(trace.keys()),
        "tier": tier,
        "cost_currency": currency,
        "id_sources": _id_sources(tg),
        "notes": notes,
    }


def render_probe(report: dict) -> str:
    lines = [f"endpoint   : {report['url']}",
             f"schema     : {report['types']} types · hash {report['schema_hash']} · "
             f"{len(report['root_fields'])} root fields"]
    trace = ", ".join(report["trace_observed"]) or "absent"
    lines.append(f"cost trace : {trace}")
    lines.append(f"tier today : {report['tier']} · cost_currency \"{report['cost_currency']}\""
                 "   (set these in the adapter as-is)")
    if report["id_sources"]:
        lines.append("id sources : real IDs are harvestable without a curated list:")
        for s in report["id_sources"]:
            page = f", paginate via {s['pagination_arg']}" if s["pagination_arg"] else ""
            lines.append(f"    {{ {s['path'].replace('.', ' { ')} {{ id }}"
                         + " }" * s["path"].count(".") + f" }}   -> {s['entity']} ids{page}")
    else:
        lines.append("id sources : none found (every list root needs arguments): "
                     "collect real IDs by hand for the adapter's ID tables")
    for n in report["notes"]:
        lines.append(f"note       : {n}")
    lines.append("next       : write the adapter (skills/costql-adapter walks an agent "
                 "through it; docs at https://costql.com/docs/adapters/)")
    return "\n".join(lines)
