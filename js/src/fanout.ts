/** Fanout-based invocation counter: port of costql/fanout.py. Walks the
 * selection tree alongside the schema and multiplies pagination bounds down
 * the tree. Pure query-visible computation, identical record order to Python
 * (pre-order, children in query order) so float summation order matches. */
import { Field, TypeGraph, isList, resolverId } from "./introspect.js";
import { Selection } from "./queryIr.js";

export interface InvocationRecord {
  resolverId: string;
  fieldPath: string;
  invocations: number;
  listSize: number;
  isLeaf: boolean;
}

export class FanoutCounter {
  constructor(private tg: TypeGraph, private defaultCap = 50) {}

  private paginationValue(f: Field, args: Record<string, unknown>, mode: string): number | null {
    const pag = this.tg.paginationArg(f);
    if (pag === null) return null;
    const requested = args[pag.name];
    const schemaMax = pag.name === "limit" || pag.name === "first" || pag.name === "last"
      ? 100 : this.defaultCap;
    if (mode === "ceiling") {
      const candidates = [requested, schemaMax].filter(
        (c): c is number => typeof c === "number");
      return candidates.length ? Math.trunc(Math.min(...candidates)) : null;
    }
    return typeof requested === "number" ? Math.trunc(requested) : null;
  }

  private listSize(f: Field, args: Record<string, unknown>, mode: string,
                   inherited: number[], sizeCaps: Record<string, number>): number {
    const own = this.paginationValue(f, args, mode);
    const observed = sizeCaps[resolverId(f)];
    const candidates = [own, inherited.length ? inherited[inherited.length - 1] : null,
                        observed].filter((c): c is number => c != null);
    if (candidates.length) return Math.trunc(Math.min(...candidates));
    return this.defaultCap;
  }

  count(rootSelections: Selection[], mode: string = "ceiling",
        sizeCaps: Record<string, number> = {}): InvocationRecord[] {
    const records: InvocationRecord[] = [];

    const walk = (sel: Selection, parentType: string, parentInvocations: number,
                  path: string, inherited: number[]): void => {
      if (sel.on != null) {
        // Fragment node: no resolver of its own. Children resolve against the
        // type condition; when that is a branch of an abstract parent, every
        // branch is walked (at most one fires per object, so the sum is a
        // safe upper bound: pack.quote attaches a caveat).
        for (const c of sel.children) {
          walk(c, sel.on, parentInvocations, `${path}.${c.name}`, inherited);
        }
        return;
      }
      const obj = this.tg.objects.get(parentType);
      if (!obj) return;
      const f = obj.fields.get(sel.name);
      if (!f) return;
      const base = f.type.base;
      const leaf = this.tg.isLeaf(base);
      let childInherited = [...inherited];
      let size: number;
      if (isList(f.type)) {
        size = this.listSize(f, sel.args, mode, inherited, sizeCaps);
        if (this.paginationValue(f, sel.args, mode) === null && childInherited.length) {
          childInherited.pop();
        }
      } else {
        size = 1;
        const pv = this.paginationValue(f, sel.args, mode);
        if (pv !== null) childInherited.push(pv);
      }
      records.push({ resolverId: resolverId(f), fieldPath: path,
                     invocations: parentInvocations, listSize: size, isLeaf: leaf });
      const childInvocations = parentInvocations * size;
      for (const c of sel.children) {
        walk(c, base, childInvocations, `${path}.${c.name}`, childInherited);
      }
    };

    for (const sel of rootSelections) {
      walk(sel, this.tg.queryType, 1, sel.name, []);
    }
    return records;
  }
}
