"""Build a pricing pack from a live GraphQL API: the calibrate → pack path.

This is the seller-side entry point. Given an :class:`~costql.config.APIConfig`
(the ~90-line adapter that says where the API is and how to fill in its
arguments), it introspects the endpoint, measures a set of clean calibration
queries, fits the cost model, learns sharing/batch curves when the server emits
a cost trace (T2/T3), samples any declared bounded fields in isolation, and
returns a :class:`~costql.pack.PricingPack`; ONE self-contained file a consumer
prices against locally, with no server and no network.

Calibration deliberately uses predictable, isolating shapes: NOT deep cyclic
coverage panels, whose data-dependent recursion corrupts a linear fit. Adapters
should supply curated shapes via ``APIConfig.calibration_queries`` (all three
reference adapters do); without them a conservative shallow coverage sweep is
used as a fallback.

Building measures the real API (that is inherent: you calibrate by measuring),
so this module needs the ``requests`` extra:  pip install 'costql[build]'.
Consuming the resulting pack does not.
"""
from __future__ import annotations

import statistics
from urllib.parse import urlparse

from .batchmodel import attach_loader_curves, recalibrate_safety
from .config import APIConfig
from .coverage import CoverageGenerator
from .exact import ExactPrice
from .harness import Harness, _requests
from .inputs import ArgProvider
from .introspect import INTROSPECTION_QUERY, TypeGraph
from .observe import observe_sizes
from .pack import PricingPack
from .pricer import DOC_FEATURE, CostModelBuilder
from .query_ir import count_fields, parse_query
from .sharing import build_sharing_model


def introspect_endpoint(url: str, headers: dict | None = None) -> dict:
    """POST the standard introspection query and return the raw JSON response
    (the shape :class:`TypeGraph` and packs store)."""
    requests = _requests()
    r = requests.post(url, json={"query": INTROSPECTION_QUERY},
                      headers=headers or {}, timeout=30)
    return r.json()


def endpoint_up(url: str) -> bool:
    """Cheap reachability probe (a `__typename` POST)."""
    requests = _requests()
    try:
        requests.post(url, json={"query": "{ __typename }"}, timeout=5)
        return True
    except requests.RequestException:
        return False


def _calibration_queries(config: APIConfig, tg: TypeGraph, mined) -> list[str]:
    if config.calibration_queries is not None:
        out = []
        for size in ("whale", "small"):
            out.extend(config.calibration_queries(size))
        return out
    # Fallback: a shallow coverage sweep (type_budget=1 keeps recursive types from
    # re-entering, so the shapes stay predictable). Curated shapes fit better.
    gen = CoverageGenerator(tg, ArgProvider(config, mined, "calib"), config,
                            max_depth=3, type_budget=1)
    out = []
    for size in ("whale", "small"):
        out.extend(op.query for op in gen.build_all(size) if op.query)
    return out


def build_pack(config: APIConfig, *, repeats: int = 5,
               verbose: bool = True) -> PricingPack:
    """Calibrate ``config``'s live endpoint and return the pricing pack.

    ``repeats``: measurement rounds per query (sample-level round-robin, so
    host drift is common-mode). Any bounded field that calls an outside host is
    sampled in isolation; costQL records the host it OBSERVED (never a fee: it
    can't know what the outside service charges; the consuming app prices it).
    """
    def say(msg: str) -> None:
        if verbose:
            print(msg, flush=True)

    if not endpoint_up(config.graphql_url):
        raise ConnectionError(
            f"GraphQL endpoint not reachable at {config.graphql_url}")
    introspection = introspect_endpoint(config.graphql_url)
    tg = TypeGraph(introspection)
    mined = config.input_source.mine()
    if mined.tokens:
        config.tokens = mined.tokens
    h = Harness(config)

    # --- calibration ops: curated (recommended) or shallow-sweep fallback ---
    calib_ops, seen = [], set()
    for q in _calibration_queries(config, tg, mined):
        if q not in seen:
            seen.add(q)
            calib_ops.append((q, parse_query(q)[0]))
    if not calib_ops:
        raise RuntimeError("no calibration queries could be generated: "
                           "supply APIConfig.calibration_queries")

    # --- bounded fields (e.g. paid external calls) sampled once in isolation ---
    iso = []
    if config.bounded_fields:
        iso = CoverageGenerator(tg, ArgProvider(config, mined, "calib"), config,
                                max_depth=2, type_budget=2).build_isolation()

    say(f"calibrating {len(calib_ops)} queries x {repeats} rounds"
        + (f"  (+{len(iso)} bounded-field isolation ops)" if iso else ""))
    jobs = [(("c", i), q, None) for i, (q, _s) in enumerate(calib_ops)]
    jobs += [(("i", i), op.query, op.auth) for i, op in enumerate(iso)]
    meas = h.measure_batch(jobs, rounds=repeats, warmup_rounds=1)

    calib, sizes, traced = [], {}, False
    for i, (q, sel) in enumerate(calib_ops):
        m = meas[("c", i)]
        if not m.ok:
            say(f"  ! calibration query failed (skipped): {q}")
            continue
        sz = observe_sizes(tg, [sel], m.response["data"])
        inv = {r: len(s) for r, s in sz.items()}
        inv[DOC_FEATURE] = count_fields([sel])
        if m.work_ms is not None:
            traced = True
        calib.append({"inv": inv, "work": m.cost_ms, "trace": m.cost_trace,
                      "query": q})
        for r, s in sz.items():
            sizes.setdefault(r, []).extend(s)
    if not calib:
        raise RuntimeError("no calibration queries succeeded against the endpoint")

    # --- fit ---
    max_size = {r: max(s) for r, s in sizes.items() if s}
    typical_size = {r: max(1, round(statistics.mean(s))) for r, s in sizes.items() if s}
    model = CostModelBuilder(tg, default_cap=config.default_cap).build(
        [(r["inv"], r["work"]) for r in calib], uncovered_edges=[],
        max_size=max_size, typical_size=typical_size)
    model.cost_currency = "work_ms" if traced else "wall_time_ms"

    # --- sharing + size-aware batch curves: only when the server emits a trace ---
    if traced:
        sm = build_sharing_model(
            [{"op": r["query"], "trace": r["trace"],
              "invocations": r["trace"].get("invocations", r["inv"])}
             for r in calib], tg=tg, known_loaders=config.known_loaders)
        model.batch_groups = dict(sm.batch_group)
        attach_loader_curves(model, [r["trace"] for r in calib])
    recalibrate_safety(tg, model, [{"query": r["query"], "work": r["work"]}
                                   for r in calib])

    # --- fold each bounded field's isolated per-invocation cost into its unit cost,
    #     and record any outside host costQL observed it call (host only, no fee) ---
    primary_host = urlparse(config.graphql_url).hostname
    for i, op in enumerate(iso):
        m = meas[("i", i)]
        if m.ok and m.work_ms is not None:
            observed = ExactPrice.from_cost_trace(
                m.cost_trace, primary_host=primary_host).external_hosts
            for rid in op.fired_resolvers:
                if rid in config.bounded_fields:
                    model.unit_cost[rid] = round(
                        m.resolver_work_ms.get(rid, m.work_ms), 2)
                    if observed:
                        model.external_hosts[rid] = observed[0]

    tier = config.tier
    if not traced and tier != "T1":
        say(f"  ! endpoint emitted no cost trace: pack honestly downgraded "
            f"{tier} -> T1 (wall-clock)")
        tier = "T1"

    schema_hash = tg.schema_hash()
    pack = PricingPack(schema_hash=schema_hash, currency=model.cost_currency,
                       introspection=introspection, model=model, tier=tier)
    say(f"built pack: schema {schema_hash} · tier {tier} · "
        f"{len(model.unit_cost)} resolver costs · currency {model.cost_currency}")
    return pack
