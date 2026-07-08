/** Query IR: a direct port of the Python engine's parser (costql/query_ir.py),
 * byte-for-byte in behavior so JS and Python quotes agree exactly.
 *
 * The parser covers the executable query surface (v0.2): named/anonymous
 * query operations, fields with arguments (scalar, enum, list, and
 * input-object literals), aliases (resolved to the schema field name;
 * duplicated aliased fields are kept and priced once each), named and inline
 * fragments (spreads are expanded in place; cycles and unknown names throw),
 * variables (substituted from the `variables` argument or the declared
 * default; a variable with neither is dropped so the ceiling's worst-case
 * bound applies), and directives (parsed and ignored: @skip/@include are
 * priced as included, which never under-prices).
 *
 * An inline fragment (or an expanded named-fragment spread) becomes a
 * Selection with `on` set to its type condition and `name` = "on:<Type>"
 * (the "on:" prefix cannot collide with a field: GraphQL names cannot
 * contain a colon). Fragment nodes carry no resolver and contribute nothing
 * to countFields; their children are counted per spread site. */

export type ArgValue =
  | string | number | boolean | null | EnumValue
  | ArgValue[] | { [key: string]: ArgValue };

export class EnumValue {
  constructor(public name: string) {}
}

export interface Selection {
  name: string;
  args: Record<string, ArgValue>;
  children: Selection[];
  on?: string | null; // inline-fragment type condition (name = "on:<Type>")
}

const ON_PREFIX = "on:";

class VarRef {
  constructor(public name: string) {}
}

class Spread {
  constructor(public name: string) {}
}

type RawSelection = Selection | Spread;

const TOKEN = /(\s+|#[^\n]*)|(\.\.\.)|([{}():,[\]!$=@])|("(?:[^"\\]|\\.)*")|(-?\d+\.\d+|-?\d+)|([_A-Za-z][_A-Za-z0-9]*)/y;

type Token = { kind: "spread" | "punct" | "string" | "number" | "name"; value: string };

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
    if (m[2] !== undefined) out.push({ kind: "spread", value: m[2] });
    else if (m[3] !== undefined) out.push({ kind: "punct", value: m[3] });
    else if (m[4] !== undefined) out.push({ kind: "string", value: m[4] });
    else if (m[5] !== undefined) out.push({ kind: "number", value: m[5] });
    else out.push({ kind: "name", value: m[6]! });
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

  // ---- document ----------------------------------------------------------

  parseDocument(): {
    operation: RawSelection[];
    fragments: Map<string, { cond: string; children: RawSelection[] }>;
    defaults: Map<string, unknown>;
  } {
    let operation: RawSelection[] | null = null;
    const fragments = new Map<string, { cond: string; children: RawSelection[] }>();
    const defaults = new Map<string, unknown>();
    while (this.peek() !== null) {
      const v = this.peek()!.value;
      if (v === "fragment") {
        this.next();
        const fname = this.next().value;
        this.expect("on");
        const cond = this.next().value;
        this.parseDirectives();
        this.expect("{");
        fragments.set(fname, { cond, children: this.parseSelectionSet() });
      } else if (v === "query" || v === "mutation" || v === "subscription" || v === "{") {
        if (operation !== null) {
          throw new Error("multiple operations in one document; pass exactly one");
        }
        operation = this.parseOperation(defaults);
      } else {
        throw new Error(`Unexpected token ${JSON.stringify(v)} at document level`);
      }
    }
    if (operation === null) throw new Error("no operation in document");
    return { operation, fragments, defaults };
  }

  private parseOperation(defaults: Map<string, unknown>): RawSelection[] {
    const p = this.peek();
    if (p && (p.value === "query" || p.value === "mutation" || p.value === "subscription")) {
      this.next();
      if (this.peek()?.kind === "name") this.next(); // optional operation name
      if (this.peek()?.value === "(") this.parseVariableDefs(defaults);
      this.parseDirectives();
    }
    this.expect("{");
    return this.parseSelectionSet();
  }

  private parseVariableDefs(defaults: Map<string, unknown>): void {
    this.expect("(");
    for (;;) {
      const p = this.peek();
      if (p?.value === ")") { this.next(); break; }
      if (p?.value === ",") { this.next(); continue; }
      this.expect("$");
      const vname = this.next().value;
      this.expect(":");
      this.consumeType();
      if (this.peek()?.value === "=") {
        this.next();
        defaults.set(vname, this.parseValue());
      }
      this.parseDirectives();
    }
  }

  /** Consume a type reference: Name / [Type], each optionally NON_NULL. */
  private consumeType(): void {
    if (this.peek()?.value === "[") {
      this.next();
      this.consumeType();
      this.expect("]");
    } else {
      this.next(); // the type name
    }
    if (this.peek()?.value === "!") this.next();
  }

  /** Consume `@name(args?)*` and discard: directives never lower a price, so
   * @skip/@include are priced as included (a safe upper bound). */
  private parseDirectives(): void {
    while (this.peek()?.value === "@") {
      this.next();
      this.next(); // directive name
      if (this.peek()?.value === "(") this.parseArgs();
    }
  }

  // ---- selections ----------------------------------------------------------

  private parseSelectionSet(): RawSelection[] {
    const sels: RawSelection[] = [];
    for (;;) {
      const p = this.peek();
      if (p?.value === "}") { this.next(); break; }
      if (p === null) break;
      if (p.kind === "spread") {
        this.next();
        sels.push(...this.parseAfterSpread());
      } else {
        sels.push(this.parseField());
      }
    }
    return sels;
  }

  private parseAfterSpread(): RawSelection[] {
    const p = this.peek();
    if (p?.value === "on") { // inline fragment with condition
      this.next();
      const cond = this.next().value;
      this.parseDirectives();
      this.expect("{");
      const children = this.parseSelectionSet() as Selection[];
      return [{ name: `${ON_PREFIX}${cond}`, args: {}, children, on: cond }];
    }
    if (p?.value === "{") { // condition-less: same type, splice
      this.next();
      return this.parseSelectionSet();
    }
    if (p?.kind === "name") { // named fragment spread
      this.next();
      this.parseDirectives();
      return [new Spread(p.value)];
    }
    if (p?.value === "@") { // `... @dir { }` condition-less
      this.parseDirectives();
      this.expect("{");
      return this.parseSelectionSet();
    }
    throw new Error(`Unexpected token ${JSON.stringify(p?.value)} after '...'`);
  }

  private parseField(): Selection {
    let name = this.next().value;
    if (this.peek()?.value === ":") { // alias: keep the FIELD name; the
      this.next();                    // response key never changes cost
      name = this.next().value;
    }
    let args: Record<string, ArgValue> = {};
    if (this.peek()?.value === "(") args = this.parseArgs();
    this.parseDirectives();
    let children: Selection[] = [];
    if (this.peek()?.value === "{") {
      this.next();
      children = this.parseSelectionSet() as Selection[];
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
      args[argname] = this.parseValue() as ArgValue;
    }
    return args;
  }

  private parseValue(): unknown {
    const tok = this.next();
    if (tok.kind === "string") return tok.value.slice(1, -1);
    if (tok.kind === "number") return Number(tok.value);
    if (tok.value === "$") {
      return new VarRef(this.next().value);
    }
    if (tok.value === "[") {
      const out: unknown[] = [];
      for (;;) {
        const p = this.peek();
        if (p?.value === "]") { this.next(); return out; }
        if (p?.value === ",") { this.next(); continue; }
        out.push(this.parseValue());
      }
    }
    if (tok.value === "{") {
      const obj: Record<string, unknown> = {};
      for (;;) {
        const p = this.peek();
        if (p?.value === "}") { this.next(); return obj; }
        if (p?.value === ",") { this.next(); continue; }
        const fieldname = this.next().value;
        this.expect(":");
        obj[fieldname] = this.parseValue();
      }
    }
    if (tok.kind === "name") {
      if (tok.value === "true") return true;
      if (tok.value === "false") return false;
      if (tok.value === "null") return null;
      return new EnumValue(tok.value);
    }
    throw new Error(`Unexpected value token ${JSON.stringify(tok.value)}`);
  }
}

// ---- post-parse: fragment expansion + variable substitution ----------------

function expandSpreads(
  sels: RawSelection[],
  fragments: Map<string, { cond: string; children: RawSelection[] }>,
  stack: string[],
): Selection[] {
  const out: Selection[] = [];
  for (const s of sels) {
    if (s instanceof Spread) {
      const frag = fragments.get(s.name);
      if (!frag) throw new Error(`unknown fragment ${JSON.stringify(s.name)}`);
      if (stack.includes(s.name)) {
        throw new Error(`fragment cycle: ${[...stack, s.name].join(" -> ")}`);
      }
      out.push({
        name: `${ON_PREFIX}${frag.cond}`, args: {}, on: frag.cond,
        children: expandSpreads(frag.children, fragments, [...stack, s.name]),
      });
    } else {
      s.children = expandSpreads(s.children as RawSelection[], fragments, stack);
      out.push(s);
    }
  }
  return out;
}

/** Replace VarRef inside container literals (a missing one becomes null; a
 * missing TOP-LEVEL VarRef drops the argument instead, in resolveVars). */
function subst(value: unknown, values: Map<string, unknown>): unknown {
  if (value instanceof VarRef) return values.has(value.name) ? values.get(value.name) : null;
  if (Array.isArray(value)) return value.map((x) => subst(x, values));
  if (value !== null && typeof value === "object" && !(value instanceof EnumValue)) {
    const out: Record<string, unknown> = {};
    for (const [k, x] of Object.entries(value)) out[k] = subst(x, values);
    return out;
  }
  return value;
}

function resolveVars(sels: Selection[], values: Map<string, unknown>): void {
  for (const s of sels) {
    for (const k of Object.keys(s.args)) {
      const v: unknown = s.args[k];
      if (v instanceof VarRef) {
        if (values.has(v.name)) {
          s.args[k] = values.get(v.name) as ArgValue;
        } else {
          // Undeclared and unprovided: drop the arg. An absent arg is the
          // ceiling-safe reading (worst-case bound applies).
          delete s.args[k];
        }
      } else if (Array.isArray(v) || (v !== null && typeof v === "object" && !(v instanceof EnumValue))) {
        s.args[k] = subst(v, values) as ArgValue;
      }
    }
    resolveVars(s.children, values);
  }
}

/** Parse an executable document (one operation + any fragment definitions)
 * into root selections. `variables` supplies values for `$name` usages;
 * declared defaults fill the gaps; a variable with neither loses its
 * argument, so the ceiling's worst-case bound applies to that field. */
export function parseQuery(s: string, variables?: Record<string, unknown> | null): Selection[] {
  const { operation, fragments, defaults } = new Parser(tokenize(s)).parseDocument();
  const sels = expandSpreads(operation, fragments, []);
  const values = new Map(defaults);
  for (const [k, v] of Object.entries(variables ?? {})) values.set(k, v);
  resolveVars(sels, values);
  return sels;
}

/** Total selection nodes in the query document. Fragment nodes count 0 (pure
 * grouping); their children count once per spread site, so a query desugared
 * by hand prices identically to its sugared form. */
export function countFields(selections: Selection[]): number {
  let n = 0;
  for (const s of selections) n += (s.on != null ? 0 : 1) + countFields(s.children);
  return n;
}
