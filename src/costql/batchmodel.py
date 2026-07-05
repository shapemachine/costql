"""Per-loader batch size -> cost curve (wires DECISIONS #1 to the sharing dimension).

The flat fold (Pricer.price) charged a shared/batched loader "once" regardless of
how many distinct rows the batch pulled, so it under-priced big batched reads (a
300-row batch priced like a 3-row one: measured in NORTHWIND_RESULT.md). This
fits, per loader, a DATA-DRIVEN curve `work_ms = f(distinct_batch_keys)` from the
real calibration traces, so the folded cost tracks the batch's actual size.

It is fully API-agnostic and LEARNED, never assumed:
  * a network loader whose batched call ~= a single call (TMDB fetches one id per
    call) yields points that fit a ~FLAT / linear-through-origin curve;
  * a DB loader whose one `SELECT ... WHERE id IN (...)` grows with rows (Northwind)
    yields a RISING curve;
both come out of the same `_fit_shape` (const/linear/log by residual, DECISIONS #1).

`SizeFn.eval` already clamps its input at `cap`, so setting `cap` = the loader's
observed max distinct-key count makes an 8-row hub table cap the batch at 8 for
free: the "bounded by the hub entity's observed cardinality" rule.
"""
from __future__ import annotations

import numpy as np

from .sizemodel import SizeFn, _fit_shape


def collect_loader_samples(calib_traces: list[dict]) -> dict[str, list[tuple]]:
    """From per-op cost traces, one (distinct_keys, work_ms) point per loader per op.

    calib_traces: list of `cost_trace` dicts (response.extensions.cost_trace). Each
    loader entry carries `batched_keys` (distinct rows actually fetched) and
    `work_ms` (that loader's own summed SQL/HTTP time)."""
    samples: dict[str, list[tuple]] = {}
    for tr in calib_traces:
        for lid, st in ((tr or {}).get("loaders") or {}).items():
            keys = st.get("batched_keys", 0)
            work = st.get("work_ms")
            if keys and keys > 0 and isinstance(work, (int, float)):
                samples.setdefault(lid, []).append((int(keys), float(work)))
    return samples


def fit_loader_curves(samples: dict[str, list[tuple]]) -> dict[str, SizeFn]:
    """samples: {loader_id: [(distinct_keys, work_ms), ...]} -> {loader_id: SizeFn}.

    Samples are BINNED by distinct-key count and reduced to the median work per
    size, so (a) many duplicate size-1 measurements don't flood the fit and (b)
    per-call jitter is cancelled: one clean representative point per observed size,
    which is what a size sweep is meant to give (DECISIONS #1). `cap` = max observed
    distinct-key count (the loader's observed cardinality bound); `safety` makes the
    ceiling >= every binned point."""
    import statistics
    out: dict[str, SizeFn] = {}
    for lid, raw in samples.items():
        by_size: dict[int, list[float]] = {}
        for k, w in raw:
            if k and k > 0:
                by_size.setdefault(int(k), []).append(float(w))
        if not by_size:
            continue
        pts = sorted((s, statistics.median(ws)) for s, ws in by_size.items())
        sizes = np.array([p[0] for p in pts], dtype=float)
        costs = np.array([p[1] for p in pts], dtype=float)
        if len(pts) >= 2:
            kind, base, slope, rp90 = _fit_shape(sizes, costs)
        else:                       # only one distinct size seen -> flat at its median
            kind, base, slope, rp90 = "const", float(costs[0]), 0.0, 0.0
        cap = int(max(sizes))       # observed cardinality bound (e.g. an 8-row table)
        fn = SizeFn(root=lid, kind=kind, base=max(0.0, base), slope=slope, cap=cap,
                    residual_p90=rp90, points=pts)
        ratios = [c / fn.eval(s) for s, c in pts if fn.eval(s) > 1e-9]
        fn.safety = round(max([1.0] + ratios), 4)
        out[lid] = fn
    return out


# ---- (de)serialization for the pricing pack ---------------------------------

_FIELDS = ("root", "kind", "base", "slope", "cap", "residual_p90", "safety",
           "typical")


def fn_to_dict(fn: SizeFn, typical: float) -> dict:
    return {"root": fn.root, "kind": fn.kind, "base": round(fn.base, 6),
            "slope": round(fn.slope, 6), "cap": fn.cap,
            "safety": fn.safety, "typical": typical,
            "points": fn.points}


def fn_from_dict(d: dict) -> SizeFn:
    fn = SizeFn(root=d["root"], kind=d["kind"], base=d["base"], slope=d["slope"],
                cap=d.get("cap"), residual_p90=d.get("residual_p90", 0.0),
                points=d.get("points", []))
    fn.safety = d.get("safety", 1.0)
    return fn


def _typical(pts: list[tuple]) -> float:
    import statistics
    return round(statistics.mean([k for k, _ in pts]), 2) if pts else 1.0


def attach_loader_curves(model, calib_traces: list[dict]) -> dict[str, SizeFn]:
    """Fit per-loader curves from calibration traces and serialize them onto the
    (work-ms) model as `loader_fns`. Returns the fitted SizeFns for inspection."""
    samples = collect_loader_samples(calib_traces)
    fns = fit_loader_curves(samples)
    model.loader_fns = {lid: fn_to_dict(fn, _typical(samples.get(lid, [])))
                        for lid, fn in fns.items()}
    return fns


def recalibrate_safety(tg, model, calib: list[dict]) -> float:
    """Re-derive the ceiling safety multiplier against the CURRENT (curve-based)
    pricing, so the ceiling still covers every calibration op after the size-aware
    fold changed the predictions. `calib`: list of {query, work}."""
    from .pricer import Pricer
    from .query_ir import parse_query
    model.safety = 1.0
    pricer = Pricer(tg, model)
    ratios = [1.0]
    for r in calib:
        pred = pricer.price(parse_query(r["query"]), mode="ceiling", fold=True).score
        if pred > 1e-9 and r.get("work"):
            ratios.append(r["work"] / pred)
    model.safety = round(max(ratios), 4)
    return model.safety
