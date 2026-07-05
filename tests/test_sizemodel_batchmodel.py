"""Size functions and the size-aware batch curves (the fix that closed the
Northwind fold-to-once under-pricing: a DB loader's batched read must be priced
by its learned size->cost curve, not counted once at a flat rate)."""
import numpy as np

from costql.batchmodel import collect_loader_samples, fit_loader_curves, fn_from_dict, fn_to_dict
from costql.sizemodel import SizeFn, _fit_shape


def test_fit_shape_recovers_a_line():
    kind, base, slope, _ = _fit_shape(np.array([1, 5, 10, 20], float),
                                      np.array([2, 10, 20, 40], float))
    assert kind == "linear"
    assert abs(slope - 2.0) < 0.05 and abs(base) < 0.5


def test_fit_shape_flat_loader_is_const():
    kind, base, _, _ = _fit_shape(np.array([1, 5, 10, 20], float),
                                  np.array([3.0, 3.1, 2.9, 3.0], float))
    assert kind == "const"
    assert abs(base - 3.0) < 0.3


def test_sizefn_clamps_at_cap():
    fn = SizeFn(root="x", kind="linear", base=0.0, slope=2.0, cap=20,
                residual_p90=0.0, points=[])
    assert abs(fn.eval(10) - 20.0) < 1e-6
    assert abs(fn.eval(100) - fn.eval(20)) < 1e-6      # the 8-row-hub rule


def test_loader_curves_from_traces_rising_vs_flat():
    traces = []
    for k in (2, 8, 20, 40):
        traces.append({"loaders": {
            "db": {"batched_keys": k, "work_ms": 0.5 * k},      # rising (DB IN-list)
            "http": {"batched_keys": k, "work_ms": 12.0},       # flat (per-call network)
        }})
    fns = fit_loader_curves(collect_loader_samples(traces))
    assert fns["db"].eval(40) > 3 * fns["db"].eval(4)
    assert abs(fns["http"].eval(40) - fns["http"].eval(2)) < 2.0


def test_curve_serialization_round_trip():
    fn = SizeFn(root="db", kind="linear", base=0.1, slope=0.5, cap=40,
                residual_p90=0.2, points=[(2, 1.0), (40, 20.0)])
    fn.safety = 1.25
    d = fn_to_dict(fn, typical=12.0)
    again = fn_from_dict(d)
    for size in (1, 10, 40, 100):
        assert abs(again.eval(size) - fn.eval(size)) < 1e-9
    assert again.safety == 1.25


def test_northwind_t3_pack_carries_rising_db_curves(packs):
    fns = packs["northwind_t3"].model.loader_fns
    assert "categories" in fns and "products" in fns
    prod = fn_from_dict(fns["products"])
    # a wider batch must cost more than a near-singleton one (the fixed bug)
    assert prod.eval(min(30, prod.cap or 30)) > prod.eval(1)
