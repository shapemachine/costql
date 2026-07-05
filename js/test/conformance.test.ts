/** The conformance suite: the TS port must reproduce the Python engine's
 * frozen oracle — every corpus quote, deep-equal, numbers within the oracle's
 * tolerance policy (max(absolute, relative*|expected|)). */
import { readFileSync } from "node:fs";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { PricingPack, validate } from "../src/index.js";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..", "..");
const oracle = JSON.parse(readFileSync(join(ROOT, "conformance", "quotes.json"), "utf8"));
const contractExamples = JSON.parse(
  readFileSync(join(ROOT, "packs", "contract_examples.json"), "utf8"));

const TOL = oracle.tolerance as { relative: number; absolute: number };

function assertDeepClose(actual: unknown, expected: unknown, path: string): void {
  if (typeof expected === "number" && typeof actual === "number") {
    const tol = Math.max(TOL.absolute, TOL.relative * Math.abs(expected));
    if (Math.abs(actual - expected) > tol) {
      throw new Error(`${path}: ${actual} != ${expected} (tol ${tol})`);
    }
    return;
  }
  if (Array.isArray(expected)) {
    if (!Array.isArray(actual) || actual.length !== expected.length) {
      throw new Error(`${path}: array mismatch (${JSON.stringify(actual)} vs ${JSON.stringify(expected)})`);
    }
    expected.forEach((e, i) => assertDeepClose((actual as unknown[])[i], e, `${path}[${i}]`));
    return;
  }
  if (expected !== null && typeof expected === "object") {
    if (actual === null || typeof actual !== "object") {
      throw new Error(`${path}: expected object, got ${JSON.stringify(actual)}`);
    }
    const ek = Object.keys(expected as object).sort();
    const ak = Object.keys(actual as object).sort();
    if (JSON.stringify(ek) !== JSON.stringify(ak)) {
      throw new Error(`${path}: key sets differ (${ak} vs ${ek})`);
    }
    for (const k of ek) {
      assertDeepClose((actual as any)[k], (expected as any)[k], `${path}.${k}`);
    }
    return;
  }
  if (actual !== expected) {
    throw new Error(`${path}: ${JSON.stringify(actual)} != ${JSON.stringify(expected)}`);
  }
}

const packCache = new Map<string, PricingPack>();
function pack(rel: string): PricingPack {
  if (!packCache.has(rel)) {
    packCache.set(rel, PricingPack.fromJSON(readFileSync(join(ROOT, rel), "utf8")));
  }
  return packCache.get(rel)!;
}

describe("conformance against the Python oracle", () => {
  for (const entry of oracle.quotes) {
    it(`${entry.pack.split("/").pop()} :: ${entry.query.slice(0, 60)}`, () => {
      const result = pack(entry.pack).quote(entry.query);
      assertDeepClose(result, entry.expected, "quote");
    });
  }
});

describe("contract validator agrees with the frozen examples", () => {
  it("all committed examples validate clean", () => {
    expect(contractExamples.contract_violations).toBe(0);
    for (const ex of contractExamples.examples) {
      expect(validate(ex.result), ex.label).toEqual([]);
    }
  });

  it("catches removed core fields", () => {
    const base = contractExamples.examples[0].result;
    for (const field of ["contract_version", "tier", "basis", "currency",
                         "price", "confidence", "schema_hash", "caveats"]) {
      const broken = JSON.parse(JSON.stringify(base));
      delete broken[field];
      expect(validate(broken).length, field).toBeGreaterThan(0);
    }
  });

  it("enforces tier gating", () => {
    const base = JSON.parse(JSON.stringify(
      contractExamples.examples.find((e: any) => e.tier === "T1" && e.basis === "predicted").result));
    base.breakdown = [{ resolver_id: "Q.x", cost: 1.0, invocations: 1 }];
    expect(validate(base).some((p: string) => p.includes("breakdown"))).toBe(true);
  });
});

describe("pack version gate", () => {
  it("rejects a newer pack", () => {
    const d = JSON.parse(readFileSync(join(ROOT, "packs", "rickmorty_t1.json"), "utf8"));
    d.pack_version = 99;
    expect(() => PricingPack.fromObject(d)).toThrow(/newer/);
  });

  it("rejects a non-pack", () => {
    expect(() => PricingPack.fromObject({ hello: "world" })).toThrow(/pack_version/);
  });
});
