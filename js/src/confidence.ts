/** Per-query confidence assessment: port of costql/confidence.py, including
 * the exact caveat wording (the strings are part of the conformance surface). */
import { InvocationRecord } from "./fanout.js";
import { TypeGraph, isList } from "./introspect.js";
import { Selection } from "./queryIr.js";
import { pyRound } from "./round.js";

export interface Confidence {
  level: "high" | "medium" | "low";
  score: number;
  cyclicShare: number;
  cyclicPaths: string[];
  caveats: string[];
}

function band(score: number): "high" | "medium" | "low" {
  if (score >= 0.8) return "high";
  if (score >= 0.5) return "medium";
  return "low";
}

function dataDependentPaths(tg: TypeGraph, selection: Selection): Set<string> {
  const flagged = new Set<string>();

  const walk = (sel: Selection, parentType: string, path: string, undeclared: number): void => {
    const obj = tg.objects.get(parentType);
    if (!obj) return;
    const f = obj.fields.get(sel.name);
    if (!f) return;
    let u = undeclared;
    if (isList(f.type)) {
      const pag = tg.paginationArg(f);
      const declared = pag !== null && pag.name in (sel.args ?? {});
      if (!declared) u += 1;
    }
    if (u >= 2) flagged.add(path);
    for (const c of sel.children) walk(c, f.type.base, `${path}.${c.name}`, u);
  };

  walk(selection, tg.queryType, selection.name, 0);
  return flagged;
}

export function assess(tg: TypeGraph, records: InvocationRecord[],
                       selection: Selection | null = null): Confidence {
  let total = 0.0;
  let cyclic = 0.0;
  const cyclicPaths: string[] = [];
  for (const r of records) total += r.invocations;

  for (const r of records) {
    const parts = r.fieldPath.split(".");
    const seenTypes = new Set<string>();
    let listEdges = 0;
    let curType = tg.queryType;
    let isBelowCycle = false;
    for (const name of parts) {
      const obj = tg.objects.get(curType);
      if (!obj) break;
      const f = obj.fields.get(name);
      if (!f) break;
      const base = f.type.base;
      if (isList(f.type)) listEdges += 1;
      if (seenTypes.has(base) && (isList(f.type) || listEdges > 0)) {
        isBelowCycle = true;
      }
      seenTypes.add(base);
      curType = base;
    }
    if (isBelowCycle) {
      cyclic += r.invocations;
      if (!cyclicPaths.includes(r.fieldPath)) cyclicPaths.push(r.fieldPath);
    }
  }
  const cyclicShare = total > 0 ? cyclic / total : 0.0;

  const ddPaths = selection !== null ? dataDependentPaths(tg, selection) : new Set<string>();
  const ddHit = [...new Set(records.map((r) => r.fieldPath).filter((p) => ddPaths.has(p)))].sort();
  const cycSet = new Set(cyclicPaths);
  let uncertain = 0;
  for (const r of records) {
    if (cycSet.has(r.fieldPath) || ddPaths.has(r.fieldPath)) uncertain += r.invocations;
  }
  const share = total > 0 ? uncertain / total : 0.0;
  const score = pyRound(1.0 - share, 3);
  const caveats: string[] = [];
  if (cyclicPaths.length) {
    const shown = [...cyclicPaths].sort().slice(0, 3).join(", ");
    caveats.push(
      `cyclic recursion re-enters a type through a list edge at ` +
      `${cyclicPaths.length} path(s) (${shown}${cyclicPaths.length > 3 ? "…" : ""}); ` +
      `the static fanout counts the worst case, but the real backend dedups ` +
      `overlapping entities by an amount that only running the query reveals. ` +
      `Price is a structural upper bound, confidence downgraded; run it for the exact T3 cost.`);
  }
  if (ddHit.length) {
    const shown = ddHit.slice(0, 3).join(", ");
    caveats.push(
      `un-declared list sizes compound at ${ddHit.length} path(s) ` +
      `(${shown}${ddHit.length > 3 ? "…" : ""}); how many ` +
      `items they yield and de-duplicate to is data-dependent, so the typical ` +
      `estimate can drift (the ceiling stays a safe upper bound). Declare ` +
      `sizes (pagination) for an exact quote, or run it.`);
  }
  return { level: band(score), score, cyclicShare: pyRound(cyclicShare, 3),
           cyclicPaths, caveats };
}
