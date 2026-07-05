"""Held-Out Oracle.

Two DISJOINT query sets:
  * calibration: full-depth, whale-input coverage ops (used to fit the model).
  * held-out   : a different distribution the model never saw: shallow and
                  mid-depth shapes and small vs. whale pagination, at
                  alternate inputs (variant="heldout"), plus any API-specific
                  adversarial fixtures the config supplies.

Ground truth per held-out query = its measured cost (median wall_time_ms).
All API-specific query shapes come from config; this module is generic.
"""
from __future__ import annotations

from dataclasses import dataclass

from .config import APIConfig, MinedInputs
from .coverage import CoverageGenerator, CoverageOp
from .inputs import ArgProvider
from .introspect import TypeGraph
from .query_ir import Selection

CALIB_DEPTHS = [2, 5, 9, 12]     # depth-varied ops identify base vs marginal
HELD_DEPTHS = [3, 7]             # disjoint from calibration depths


@dataclass
class LabeledQuery:
    name: str
    root_field: str
    auth: str | None
    selections: list[Selection]
    query: str
    note: str = ""


def calibration_set(tg: TypeGraph, args: ArgProvider, config: APIConfig) -> list[CoverageOp]:
    """Depth-varied, whale-input coverage ops the model fits on."""
    ops = []
    seen = set()
    for d in CALIB_DEPTHS:
        for op in CoverageGenerator(tg, args, config, max_depth=d, type_budget=2).build_all(size="whale"):
            key = (op.root_field, op.query)
            if key in seen:
                continue
            seen.add(key)
            ops.append(op)
    return ops


def held_out_set(tg: TypeGraph, mined: MinedInputs, config: APIConfig) -> list[LabeledQuery]:
    alt = ArgProvider(config, mined, variant="heldout")   # disjoint inputs
    out: list[LabeledQuery] = []

    for d in HELD_DEPTHS:
        for op in CoverageGenerator(tg, alt, config, max_depth=d, type_budget=1).build_all(size="whale"):
            out.append(LabeledQuery(f"d{d}:{op.root_field}", op.root_field, op.auth,
                                    [op.selection], op.query, f"depth<={d}, alt input"))
    for op in CoverageGenerator(tg, alt, config, max_depth=7, type_budget=1).build_all(size="small"):
        out.append(LabeledQuery(f"small:{op.root_field}", op.root_field, op.auth,
                                [op.selection], op.query, "depth<=7, small size, alt input"))

    # API-specific adversarial fixtures (deep fanout, whale, shared-work shapes).
    if config.held_out_extra is not None:
        out.extend(config.held_out_extra(tg, mined))
    return out
