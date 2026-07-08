/** The pricing pack: port of costql/pack.py's consumer side. Load the same
 * JSON file the Python engine writes and quote queries by pure local
 * traversal: no server, no network, no measurement. */
import { assess, branchPaths } from "./confidence.js";
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

export interface PackData {
  pack_version: number;
  schema_hash: string;
  currency?: string;
  tier?: string;
  introspection: unknown;
  model: CostModelData;
}

export class PricingPack {
  readonly schemaHash: string;
  readonly currency: string;
  readonly tier: string;
  readonly introspection: unknown;
  readonly model: CostModelData;
  private tg: TypeGraph | null = null;

  private constructor(d: PackData) {
    this.schemaHash = d.schema_hash;
    this.model = d.model;
    this.currency = d.currency ?? d.model.cost_currency;
    this.tier = d.tier ?? "T3";
    this.introspection = d.introspection;
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
        `(this one reads <= ${PACK_VERSION}); upgrade costql`);
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

  /** Price a query from the pack alone: no server, no measurement. Returns a
   * frozen contract v1.0 result identical to the Python engine's.
   * `variables` supplies values for `$name` usages; a variable with no value
   * and no declared default loses its argument, so the ceiling's worst-case
   * bound applies to that field. */
  quote(query: string, variables?: Record<string, unknown> | null): QuoteResult {
    const tg = this.typeGraph();
    const model = this.model;
    const pricer = new Pricer(tg, model);
    const sels = parseQuery(query, variables);

    const ceiling = pricer.price(sels, "ceiling", true);
    const typical = pricer.price(sels, "expectation", true);
    const recs = pricer.counter.count(sels, "ceiling", model.max_size ?? {});
    const conf = assess(tg, recs, sels.length ? sels[0] : null);
    const caveats = [...conf.caveats];
    const branched = branchPaths(tg, sels);
    if (branched.length) {
      caveats.push(
        `polymorphic branches (\`... on Type\`) at ${branched.length} path(s) ` +
        `(${branched.slice(0, 3).join(", ")}${branched.length > 3 ? "…" : ""}); the ` +
        `price walks every branch, but at most one fires per object, so ` +
        `price and typical are upper bounds for those fields. Run it for ` +
        `the exact cost.`);
    }
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

    // named external calls (T3): outside hosts costQL OBSERVED at build time for the
    // resolvers this query hits, with the ceiling call count. No fee: costQL never
    // knows what the outside service charges; the consuming app prices it.
    const invByRid: Record<string, number> = {};
    for (const b of breakdown) invByRid[b.resolver_id] = Number(b.invocations ?? 1);
    const externalCalls = Object.entries(this.model.external_hosts ?? {})
      .filter(([rid]) => used.has(rid))
      .sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0))
      .map(([rid, host]) => ({ resolver_id: rid, host, calls: invByRid[rid] ?? 1 }));

    const result = predictedResult({
      tier: this.tier, currency: this.currency, schemaHash: this.schemaHash,
      price: ceiling.score, typicalPrice: typical.score, confidence: conf.level,
      caveats, breakdown, sharing, externalCalls,
    });
    result.query = query;
    return result;
  }
}
