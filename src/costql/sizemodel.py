"""Phase 3: size functions per fanout resolver + operating-point sweep.

Fits cost(result_size) for size-bearing roots from a sweep that varies result
size un-confounded from branch, chooses the shape (const/linear/log) by
residual, detects a saturation cap (e.g. a server-side clamp), and flags
scan_before_paginate nodes (cost tracks the unpaginated set, not first:N).
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np


@dataclass
class SizeFn:
    root: str
    kind: str                 # "const" | "linear" | "log"
    base: float
    slope: float
    cap: int | None        # saturation size (server clamp), or None
    arg: str = ""             # pagination arg that bounds this root's result size
    offset: int = 0           # added to the arg (e.g. graph nodes = topK + self)
    scan_before_paginate: bool = False
    residual_p90: float = 0.0
    safety: float = 1.0       # multiplier so ceiling >= every calibration point
    buffer: float = 0.0       # additive drift allowance (ms)
    points: list = field(default_factory=list)   # (size, cost) observed

    def eval(self, size: float) -> float:
        s = size if self.cap is None else min(size, self.cap)
        if self.kind == "const":
            return self.base
        if self.kind == "log":
            return self.base + self.slope * np.log1p(max(0.0, s))
        return self.base + self.slope * max(0.0, s)   # linear


def _fit_shape(sizes: np.ndarray, costs: np.ndarray):
    """Return (kind, base, slope, residual_p90) for the best of const/linear/log."""
    best = None
    candidates = {
        "const": np.ones_like(sizes),
        "linear": sizes,
        "log": np.log1p(sizes),
    }
    for kind, feat in candidates.items():
        if kind == "const":
            base = float(costs.mean()); slope = 0.0
            pred = np.full_like(costs, base)
        else:
            A = np.vstack([np.ones_like(feat), feat]).T
            coef, *_ = np.linalg.lstsq(A, costs, rcond=None)
            base, slope = float(coef[0]), float(coef[1])
            pred = A @ coef
        resid = np.abs(pred - costs) / np.maximum(costs, 1e-9)
        rp90 = float(np.percentile(resid, 90))
        # Prefer simpler shapes unless a richer one is clearly better.
        penalty = {"const": 0.0, "log": 0.01, "linear": 0.02}[kind]
        score = rp90 + penalty
        if best is None or score < best[0]:
            best = (score, kind, base, slope, rp90)
    _, kind, base, slope, rp90 = best
    return kind, base, slope, rp90


def size_of_query(fn: SizeFn, args: dict, observed_cap: int | None = None,
                  mode: str = "ceiling") -> float:
    """Result size a query implies for a size-root, from its pagination arg."""
    requested = args.get(fn.arg)
    bounds = [b for b in (requested, fn.cap, observed_cap) if b is not None]
    base = min(bounds) if bounds else (observed_cap or 1)
    return float(base + fn.offset)


def fit_size_functions(sweeps: dict, size_roots: dict) -> dict:
    """sweeps: {root: [(size, cost), ...]}, size_roots: {root: {arg, offset, cap}}
    -> {root: SizeFn}."""
    out = {}
    for root, pts in sweeps.items():
        pts = sorted(pts)
        sizes = np.array([p[0] for p in pts], dtype=float)
        costs = np.array([p[1] for p in pts], dtype=float)
        cfg = size_roots.get(root, {})
        cap = cfg.get("cap")
        kind, base, slope, rp90 = _fit_shape(sizes, costs)
        fn = SizeFn(root=root, kind=kind, base=base, slope=slope, cap=cap,
                    arg=cfg.get("arg", ""), offset=cfg.get("offset", 0),
                    residual_p90=rp90, points=pts)
        # Safety so the ceiling >= every calibration point (covers fit residual).
        ratios = [c / fn.eval(s) for s, c in pts if fn.eval(s) > 1e-9]
        fn.safety = max([1.0] + ratios)
        out[root] = fn
    return out
