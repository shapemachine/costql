/** The introspected schema, indexed for cost analysis: a direct port of the
 * Python engine's TypeGraph (costql/introspect.py). Consumes the raw GraphQL
 * introspection JSON embedded in every pricing pack; no graphql-js needed. */

export interface TypeRef {
  base: string;
  baseKind: string;
  listDepth: number;
  required: boolean;
}

export interface Arg {
  name: string;
  type: TypeRef;
  default: string | null;
}

export interface Field {
  parent: string;
  name: string;
  type: TypeRef;
  args: Arg[];
}

export interface ObjectType {
  name: string;
  kind: string; // OBJECT | INTERFACE | UNION
  fields: Map<string, Field>;
  possibleTypes: string[];
}

export const PAGINATION_ARGS = ["first", "limit", "last", "top_k", "topK", "count", "depth"] as const;

export const isList = (t: TypeRef): boolean => t.listDepth >= 1;

export const resolverId = (f: Field): string => `${f.parent}.${f.name}`;

function unwrap(t: any): TypeRef {
  let listDepth = 0;
  const requiredOuter = t?.kind === "NON_NULL";
  let base: string | null = null;
  let baseKind: string | null = null;
  let node = t;
  while (node != null) {
    const k = node.kind;
    if (k === "LIST") listDepth += 1;
    if (node.name && k !== "LIST" && k !== "NON_NULL") {
      base = node.name;
      baseKind = k;
    }
    node = node.ofType;
  }
  return { base: base ?? "Unknown", baseKind: baseKind ?? "SCALAR", listDepth, required: requiredOuter };
}

export class TypeGraph {
  queryType: string;
  objects = new Map<string, ObjectType>();
  scalars = new Set<string>();
  enums = new Map<string, string[]>();

  constructor(introspection: any) {
    const schema = introspection.data.__schema;
    this.queryType = schema.queryType.name;
    for (const t of schema.types) {
      const name: string = t.name;
      if (name.startsWith("__")) continue;
      const kind: string = t.kind;
      if (kind === "SCALAR") {
        this.scalars.add(name);
      } else if (kind === "ENUM") {
        this.enums.set(name, (t.enumValues ?? []).map((e: any) => e.name));
      } else if (kind === "OBJECT" || kind === "INTERFACE" || kind === "UNION") {
        const obj: ObjectType = {
          name, kind, fields: new Map(),
          possibleTypes: (t.possibleTypes ?? []).map((p: any) => p.name),
        };
        for (const f of t.fields ?? []) {
          const args: Arg[] = f.args.map((a: any) => ({
            name: a.name, type: unwrap(a.type), default: a.defaultValue ?? null,
          }));
          obj.fields.set(f.name, { parent: name, name: f.name, type: unwrap(f.type), args });
        }
        this.objects.set(name, obj);
      }
    }
  }

  isLeaf(typeName: string): boolean {
    return this.scalars.has(typeName) || this.enums.has(typeName);
  }

  paginationArg(f: Field): Arg | null {
    for (const pname of PAGINATION_ARGS) {
      const a = f.args.find((x) => x.name === pname);
      if (a) return a;
    }
    return null;
  }
}
