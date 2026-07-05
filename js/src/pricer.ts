/** The quote-side pricer — port of Pricer.price from costql/pricer.py.
 * Static-only: pure traversal of the pre-built model. The BUILD side (NNLS
 * fit, calibration, measurement) intentionally stays Python-only. */
import { FanoutCounter, InvocationRecord } from "./fanout.js";
import { TypeGraph } from "./introspect.js";
import { Selection, countFields } from "./queryIr.js";
import { pyRound } from "./round.js";
import { SizeFn, SizeFnData, fnFromDict } from "./sizefn.js";

export const DOC_FEATURE = "__doc_fields__";

export interface CostModelData {
  schema_hash: string;
  cost_currency: string;
  unit_cost: Record<string, number>;
  safety: number;
  default_cap: number;
  noise_buffer_ms?: number;
  batch_groups?: Record<string, string>;
  loader_fns?: Record<string, SizeFnData>;
  max_size?: Record<string, number>;
  typical_size?: Record<string, number>;
  uncovered_edges?: string[];
  scan_before_paginate?: string[];
}

export interface BreakdownLine extends Record<string, unknown> {
  resolver_id: string;
  invocations: number;
  list_size: number;
  cost: number;
}

export interface QueryCost {
  mode: string;
  score: number;
  perResolver: BreakdownLine[];
}

export class Pricer {
  readonly counter: FanoutCounter;
  private loaderFnCache: Map<string, SizeFn> | null = null;

  constructor(private tg: TypeGraph, private model: CostModelData) {
    this.counter = new FanoutCounter(tg, model.default_cap);
  }

  private loaderFns(): Map<string, SizeFn> {
    if (this.loaderFnCache === null) {
      this.loaderFnCache = new Map(
        Object.entries(this.model.loader_fns ?? {}).map(([lid, d]) => [lid, fnFromDict(d)]));
    }
    return this.loaderFnCache;
  }

  sizeCaps(mode: string): Record<string, number> {
    if (mode === "ceiling") return this.model.max_size ?? {};
    const typical = this.model.typical_size ?? {};
    return Object.keys(typical).length ? typical : (this.model.max_size ?? {});
  }

  price(rootSelections: Selection[], mode: string = "ceiling", fold = true): QueryCost {
    const records = this.counter.count(rootSelections, mode, this.sizeCaps(mode));
    const breakdown: BreakdownLine[] = [];
    let total = 0.0;
    const foldedBatches = new Set<string>();

    const docUnit = this.model.unit_cost[DOC_FEATURE] ?? 0.0;
    if (docUnit > 0) {
      const nfields = countFields(rootSelections);
      const dcost = docUnit * nfields;
      total += dcost;
      breakdown.push({ resolver_id: DOC_FEATURE, invocations: nfields,
                       list_size: 1, cost: pyRound(dcost, 4) });
    }
    const loaderFns = this.loaderFns();
    const batchGroups = this.model.batch_groups ?? {};
    for (const rec of records) {
      const unit = this.model.unit_cost[rec.resolverId] ?? 0.0;
      let invs = rec.invocations;
      let cost: number;
      const loader = batchGroups[rec.resolverId];
      if (fold && loader !== undefined) {
        if (foldedBatches.has(loader)) {
          cost = 0.0; // shared work already counted this request
          invs = 0;
        } else {
          foldedBatches.add(loader);
          const fn = loaderFns.get(loader);
          if (fn !== undefined) {
            // Size-aware fold: the batched loader runs ONE query over the
            // distinct keys; the curve adds only the learned size-dependent
            // marginal beyond the size-1 batch cost already in `unit`.
            const dKeys = rec.invocations;
            const marginal = Math.max(0.0, fn.eval(dKeys) - fn.eval(1));
            cost = unit + marginal;
            invs = fn.cap ? Math.min(dKeys, fn.cap) : dKeys;
          } else {
            invs = 1; // no curve learned -> flat one-batch fallback
            cost = unit * invs;
          }
        }
      } else {
        cost = unit * invs;
      }
      total += cost;
      if (cost > 0) {
        breakdown.push({ resolver_id: rec.resolverId, invocations: invs,
                         list_size: rec.listSize, cost: pyRound(cost, 4) });
      }
    }
    if (mode === "ceiling") {
      total = total * this.model.safety + (this.model.noise_buffer_ms ?? 0.0);
    }
    return { mode, score: pyRound(total, 4), perResolver: breakdown };
  }
}
