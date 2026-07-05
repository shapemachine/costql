/** The pricing pack — port of costql/pack.py's consumer side. Load the same
 * JSON file the Python engine writes and quote queries by pure local
 * traversal: no server, no network, no measurement. */
import { assess } from "./confidence.js";
import { QuoteResult, predictedResult } from "./contract.js";
import { TypeGraph } from "./introspect.js";
import { CostModelData, DOC_FEATURE, Pricer } from "./pricer.js";
import { parseQuery } from "./queryIr.js";

export const PACK_VERSION = 1;

export class PackVersionError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "PackVersionError";
  }
}

const REQUIRED_KEYS = ["schema_hash", "introspection", "model"] as const;

interface AdjustmentEntry {
  added_unit_cost?: number | string | null;
  downstream_host?: string;
  [k: string]: unknown;
}

export interface PackData {
  pack_version: number;
  schema_hash: string;
  currency?: string;
  tier?: string;
  introspection: unknown;
  model: CostModelData;
  adjustments?: { adjustments?: Record<string, AdjustmentEntry> } | Record<string, never>;
}

export class PricingPack {
  readonly schemaHash: string;
  readonly currency: string;
  readonly tier: string;
  readonly introspection: unknown;
  readonly model: CostModelData;
  readonly adjustments: PackData["adjustments"];
  private tg: TypeGraph | null = null;

  private constructor(d: PackData) {
    this.schemaHash = d.schema_hash;
    this.model = d.model;
    this.currency = d.currency ?? d.model.cost_currency;
    this.tier = d.tier ?? "T3";
    this.introspection = d.introspection;
    this.adjustments = d.adjustments ?? {};
  }

  /** Build from an already-parsed pack object (e.g. a fetch().json() result). */
  static fromObject(d: unknown): PricingPack {
    if (typeof d !== "object" || d === null || !("pack_version" in d)) {
      throw new PackVersionError("not a costQL pricing pack: missing 'pack_version'");
    }
    const obj = d as Record<string, unknown>;
    const version = Number(obj.pack_version);
    if (!Number.isInteger(version)) {
      throw new PackVersionError(`unreadable pack_version: ${JSON.stringify(obj.pack_version)}`);
    }
    if (version > PACK_VERSION) {
      throw new PackVersionError(
        `pack_version ${version} was written by a newer costql ` +
        `(this one reads <= ${PACK_VERSION}) — upgrade costql`);
    }
    const missing = REQUIRED_KEYS.filter((k) => !(k in obj));
    if (missing.length) {
      throw new PackVersionError(
        `pricing pack is missing required sections: ${missing.join(", ")}`);
    }
    return new PricingPack(obj as unknown as PackData);
  }

  /** Parse a pack from its JSON text. */
  static fromJSON(text: string): PricingPack {
    return PricingPack.fromObject(JSON.parse(text));
  }

  /** Node convenience: read a pack file from disk. Browser code should fetch
   * the pack and use fromObject/fromJSON instead. */
  static async load(path: string): Promise<PricingPack> {
    const { readFile } = await import("node:fs/promises");
    return PricingPack.fromJSON(await readFile(path, "utf8"));
  }

  private typeGraph(): TypeGraph {
    if (this.tg === null) this.tg = new TypeGraph(this.introspection);
    return this.tg;
  }

  /** The model with hand-authored fees folded into per-resolver unit costs. */
  private effectiveModel(): CostModelData {
    const adj = (this.adjustments as PackData["adjustments"])?.adjustments ?? {};
    const fees: Record<string, number> = {};
    for (const [rid, a] of Object.entries(adj)) {
      const raw = a?.added_unit_cost ?? 0.0;
      const fee = Number(raw || 0.0);
      if (Number.isFinite(fee) && fee) fees[rid] = fee;
    }
    if (!Object.keys(fees).length) return this.model;
    const unit = { ...this.model.unit_cost };
    for (const [rid, fee] of Object.entries(fees)) {
      unit[rid] = (unit[rid] ?? 0.0) + fee;
    }
    return { ...this.model, unit_cost: unit, batch_groups: { ...(this.model.batch_groups ?? {}) } };
  }

  /** Price a query from the pack alone — no server, no measurement. Returns a
   * frozen contract v1.0 result identical to the Python engine's. */
  quote(query: string): QuoteResult {
    const tg = this.typeGraph();
    const model = this.effectiveModel();
    const pricer = new Pricer(tg, model);
    const sels = parseQuery(query);

    const ceiling = pricer.price(sels, "ceiling", true);
    const typical = pricer.price(sels, "expectation", true);
    const recs = pricer.counter.count(sels, "ceiling", model.max_size ?? {});
    const conf = assess(tg, recs, sels.length ? sels[0] : null);
    const used = new Set(recs.map((r) => r.resolverId));

    const breakdown = ceiling.perResolver.filter((b) => b.resolver_id !== DOC_FEATURE);

    const folds = new Map<string, string[]>();
    for (const [rid, loader] of Object.entries(model.batch_groups ?? {})) {
      if (used.has(rid)) {
        if (!folds.has(loader)) folds.set(loader, []);
        folds.get(loader)!.push(rid);
      }
    }
    const sharing = [...folds.entries()]
      .sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0))
      .map(([loader, rs]) => ({ loader, folds: [...rs].sort(), counted_once: true }));

    const adj = (this.adjustments as PackData["adjustments"])?.adjustments ?? {};
    const externalCosts = Object.entries(adj)
      .filter(([rid]) => used.has(rid))
      .map(([rid, a]) => ({
        resolver_id: rid,
        host: a?.downstream_host ?? null,
        authored_fee: Number((a?.added_unit_cost ?? 0.0) || 0.0) || 0.0,
        measured_fee: false,
      }));

    const result = predictedResult({
      tier: this.tier, currency: this.currency, schemaHash: this.schemaHash,
      price: ceiling.score, typicalPrice: typical.score, confidence: conf.level,
      caveats: conf.caveats, breakdown, sharing, externalCosts,
    });
    result.query = query;
    return result;
  }
}
