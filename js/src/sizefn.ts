/** Per-loader size->cost curves — port of the eval/deserialize slice of
 * costql/sizemodel.py + costql/batchmodel.py that the quote path uses. */

export interface SizeFnData {
  root: string;
  kind: "const" | "linear" | "log";
  base: number;
  slope: number;
  cap: number | null;
  safety?: number;
  typical?: number;
  points?: Array<[number, number]>;
}

export class SizeFn {
  root: string;
  kind: string;
  base: number;
  slope: number;
  cap: number | null;
  safety: number;

  constructor(d: SizeFnData) {
    this.root = d.root;
    this.kind = d.kind;
    this.base = d.base;
    this.slope = d.slope;
    this.cap = d.cap ?? null;
    this.safety = d.safety ?? 1.0;
  }

  eval(size: number): number {
    const s = this.cap == null ? size : Math.min(size, this.cap);
    if (this.kind === "const") return this.base;
    if (this.kind === "log") return this.base + this.slope * Math.log1p(Math.max(0.0, s));
    return this.base + this.slope * Math.max(0.0, s); // linear
  }
}

export function fnFromDict(d: SizeFnData): SizeFn {
  return new SizeFn(d);
}
