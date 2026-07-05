/** Query IR — a direct port of the Python engine's parser (costql/query_ir.py),
 * byte-for-byte in behavior so JS and Python quotes agree exactly. Deliberately
 * a subset of GraphQL (the same subset): named/anonymous query operations,
 * fields with scalar arguments, nested selection sets. Fragments raise; aliases
 * are not resolved (the alias becomes the selection name) — frozen v0.1
 * limitations shared by both engines. */

export type ArgValue = string | number | boolean | null | EnumValue;

export class EnumValue {
  constructor(public name: string) {}
}

export interface Selection {
  name: string;
  args: Record<string, ArgValue>;
  children: Selection[];
}

const TOKEN = /(\s+|#[^\n]*)|([{}():,])|("(?:[^"\\]|\\.)*")|(-?\d+\.\d+|-?\d+)|([_A-Za-z][_A-Za-z0-9]*)/y;

type Token = { kind: "punct" | "string" | "number" | "name"; value: string };

function tokenize(s: string): Token[] {
  const out: Token[] = [];
  let i = 0;
  while (i < s.length) {
    TOKEN.lastIndex = i;
    const m = TOKEN.exec(s);
    if (!m || m.index !== i) {
      throw new Error(`Cannot tokenize near: ${JSON.stringify(s.slice(i, i + 20))}`);
    }
    i = TOKEN.lastIndex;
    if (m[1] !== undefined) continue; // whitespace / comment
    if (m[2] !== undefined) out.push({ kind: "punct", value: m[2] });
    else if (m[3] !== undefined) out.push({ kind: "string", value: m[3] });
    else if (m[4] !== undefined) out.push({ kind: "number", value: m[4] });
    else out.push({ kind: "name", value: m[5]! });
  }
  return out;
}

class Parser {
  private i = 0;
  constructor(private t: Token[]) {}

  private peek(): Token | null {
    return this.i < this.t.length ? this.t[this.i] : null;
  }

  private next(): Token {
    return this.t[this.i++];
  }

  private expect(val: string): void {
    const tok = this.next();
    if (!tok || tok.value !== val) {
      throw new Error(`Expected ${JSON.stringify(val)}, got ${JSON.stringify(tok?.value)}`);
    }
  }

  parseOperation(): Selection[] {
    const p = this.peek();
    if (p && (p.value === "query" || p.value === "mutation" || p.value === "subscription")) {
      this.next();
      if (this.peek()?.kind === "name") this.next(); // optional operation name
    }
    this.expect("{");
    return this.parseSelectionSet();
  }

  private parseSelectionSet(): Selection[] {
    const sels: Selection[] = [];
    for (;;) {
      const p = this.peek();
      if (p?.value === "}") { this.next(); break; }
      if (p === null) break;
      sels.push(this.parseField());
    }
    return sels;
  }

  private parseField(): Selection {
    const name = this.next().value;
    let args: Record<string, ArgValue> = {};
    if (this.peek()?.value === "(") args = this.parseArgs();
    let children: Selection[] = [];
    if (this.peek()?.value === "{") {
      this.next();
      children = this.parseSelectionSet();
    }
    return { name, args, children };
  }

  private parseArgs(): Record<string, ArgValue> {
    this.expect("(");
    const args: Record<string, ArgValue> = {};
    for (;;) {
      const p = this.peek();
      if (p?.value === ")") { this.next(); break; }
      if (p?.value === ",") { this.next(); continue; }
      const argname = this.next().value;
      this.expect(":");
      args[argname] = this.parseValue();
    }
    return args;
  }

  private parseValue(): ArgValue {
    const tok = this.next();
    if (tok.kind === "string") return tok.value.slice(1, -1);
    if (tok.kind === "number") return Number(tok.value);
    if (tok.kind === "name") {
      if (tok.value === "true") return true;
      if (tok.value === "false") return false;
      if (tok.value === "null") return null;
      return new EnumValue(tok.value);
    }
    throw new Error(`Unexpected value token ${JSON.stringify(tok.value)}`);
  }
}

export function parseQuery(s: string): Selection[] {
  return new Parser(tokenize(s)).parseOperation();
}

export function countFields(selections: Selection[]): number {
  let n = 0;
  for (const s of selections) n += 1 + countFields(s.children);
  return n;
}
