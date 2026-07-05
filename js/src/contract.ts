/** The FROZEN costQL price-result contract (v1.0) — port of costql/contract.py.
 * `price` is always present, in `currency` cost-units, safe to bill on. */
import { pyRound } from "./round.js";

export const CONTRACT_VERSION = "1.0";

const TIERS = ["T1", "T2", "T3"] as const;
const BASES = ["predicted", "measured"] as const;
const CONFIDENCE = ["high", "medium", "low", "exact"] as const;

const ALLOWED_SECTIONS: Record<string, Set<string>> = {
  T1: new Set(),
  T2: new Set(["breakdown"]),
  T3: new Set(["breakdown", "sharing", "external_costs"]),
};

export interface QuoteResult {
  contract_version: string;
  tier: string;
  basis: string;
  currency: string;
  price: number;
  typical_price: number | null;
  confidence: string;
  schema_hash: string;
  caveats: string[];
  breakdown?: Array<Record<string, unknown>>;
  sharing?: Array<Record<string, unknown>>;
  external_costs?: Array<Record<string, unknown>>;
  query?: string;
}

export function validate(result: unknown): string[] {
  const problems: string[] = [];
  if (typeof result !== "object" || result === null || Array.isArray(result)) {
    return ["result is not an object"];
  }
  const r = result as Record<string, unknown>;
  const core: Array<[string, (v: unknown) => boolean, string]> = [
    ["contract_version", (v) => typeof v === "string", "string"],
    ["tier", (v) => typeof v === "string", "string"],
    ["basis", (v) => typeof v === "string", "string"],
    ["currency", (v) => typeof v === "string", "string"],
    ["price", (v) => typeof v === "number" && !Number.isNaN(v), "number"],
    ["confidence", (v) => typeof v === "string", "string"],
    ["schema_hash", (v) => typeof v === "string", "string"],
    ["caveats", (v) => Array.isArray(v), "list"],
  ];
  for (const [key, ok, typ] of core) {
    if (!(key in r)) problems.push(`missing required field '${key}'`);
    else if (!ok(r[key])) problems.push(`field '${key}' must be ${typ}`);
  }
  if (r.contract_version !== CONTRACT_VERSION) {
    problems.push(`contract_version must be '${CONTRACT_VERSION}'`);
  }
  if (!TIERS.includes(r.tier as any)) problems.push(`tier must be one of ${TIERS.join("/")}`);
  if (!BASES.includes(r.basis as any)) problems.push(`basis must be one of ${BASES.join("/")}`);
  if (!CONFIDENCE.includes(r.confidence as any)) {
    problems.push(`confidence must be one of ${CONFIDENCE.join("/")}`);
  }
  if (typeof r.price === "number" && r.price < 0) problems.push("price must be >= 0");

  const allowed = ALLOWED_SECTIONS[r.tier as string] ?? new Set<string>();
  for (const section of ["breakdown", "sharing", "external_costs"]) {
    const v = r[section];
    if (section in r && Array.isArray(v) ? v.length : section in r && v) {
      if (!allowed.has(section)) {
        problems.push(`section '${section}' is not permitted at tier ${r.tier}`);
      } else if (!Array.isArray(v)) {
        problems.push(`section '${section}' must be a list`);
      }
    }
  }
  if ("typical_price" in r && r.typical_price !== null &&
      typeof r.typical_price !== "number") {
    problems.push("typical_price must be a number or null");
  }
  return problems;
}

export function predictedResult(opts: {
  tier: string; currency: string; schemaHash: string;
  price: number; typicalPrice: number | null; confidence: string;
  caveats?: string[];
  breakdown?: Array<Record<string, unknown>>;
  sharing?: Array<Record<string, unknown>>;
  externalCosts?: Array<Record<string, unknown>>;
}): QuoteResult {
  const result: QuoteResult = {
    contract_version: CONTRACT_VERSION,
    tier: opts.tier,
    basis: "predicted",
    currency: opts.currency,
    price: pyRound(opts.price, 4),
    typical_price: opts.typicalPrice !== null ? pyRound(opts.typicalPrice, 4) : null,
    confidence: opts.confidence,
    schema_hash: opts.schemaHash,
    caveats: [...(opts.caveats ?? [])],
  };
  const allowed = ALLOWED_SECTIONS[opts.tier];
  if (opts.breakdown?.length && allowed.has("breakdown")) result.breakdown = opts.breakdown;
  if (opts.sharing?.length && allowed.has("sharing")) result.sharing = opts.sharing;
  if (opts.externalCosts?.length && allowed.has("external_costs")) {
    result.external_costs = opts.externalCosts;
  }
  return result;
}
