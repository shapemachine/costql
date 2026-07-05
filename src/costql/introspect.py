"""Schema Introspector: turns a GraphQL introspection result into a type graph.

Schema-general: nothing here is specific to any one API. It reads the introspection
document and exposes roots, fields, fanout edges (list-typed fields), and
polymorphic branches (interface/union possibleTypes).
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field


@dataclass
class TypeRef:
    """A (possibly wrapped) reference to a named type.

    ``base`` is the innermost named type; ``list_depth`` counts LIST wrappers;
    ``required`` is True when the outermost wrapper is NON_NULL.
    """

    base: str
    base_kind: str
    list_depth: int
    required: bool

    @property
    def is_list(self) -> bool:
        return self.list_depth >= 1


@dataclass
class Arg:
    name: str
    type: TypeRef
    default: str | None

    @property
    def required(self) -> bool:
        # Required = NON_NULL and no default supplied.
        return self.type.required and self.default is None


@dataclass
class Field:
    parent: str
    name: str
    type: TypeRef
    args: list[Arg]

    @property
    def resolver_id(self) -> str:
        return f"{self.parent}.{self.name}"

    def arg(self, name: str) -> Arg | None:
        for a in self.args:
            if a.name == name:
                return a
        return None


@dataclass
class ObjectType:
    name: str
    kind: str  # OBJECT | INTERFACE | UNION
    fields: dict[str, Field] = field(default_factory=dict)
    possible_types: list[str] = field(default_factory=list)  # for INTERFACE/UNION


# Pagination argument names we recognise as fanout bounds, in priority order.
PAGINATION_ARGS = ("first", "limit", "last", "top_k", "topK", "count", "depth")


def _unwrap(t: dict) -> TypeRef:
    """Collapse the nested ofType chain into a flat TypeRef."""
    list_depth = 0
    required_outer = t.get("kind") == "NON_NULL"
    base = None
    base_kind = None
    node = t
    while node is not None:
        k = node.get("kind")
        if k == "LIST":
            list_depth += 1
        if node.get("name") and k not in ("LIST", "NON_NULL"):
            base = node["name"]
            base_kind = k
        node = node.get("ofType")
    return TypeRef(base=base or "Unknown", base_kind=base_kind or "SCALAR",
                   list_depth=list_depth, required=required_outer)


class TypeGraph:
    """The introspected schema, indexed for cost analysis."""

    def __init__(self, introspection: dict):
        schema = introspection["data"]["__schema"]
        self.query_type: str = schema["queryType"]["name"]
        self.objects: dict[str, ObjectType] = {}
        self.scalars: set[str] = set()
        self.enums: dict[str, list[str]] = {}
        self._raw = schema

        for t in schema["types"]:
            name = t["name"]
            if name.startswith("__"):
                continue
            kind = t["kind"]
            if kind == "SCALAR":
                self.scalars.add(name)
            elif kind == "ENUM":
                self.enums[name] = [e["name"] for e in (t.get("enumValues") or [])]
            elif kind in ("OBJECT", "INTERFACE", "UNION"):
                obj = ObjectType(name=name, kind=kind,
                                 possible_types=[p["name"] for p in (t.get("possibleTypes") or [])])
                for f in (t.get("fields") or []):
                    args = [Arg(name=a["name"], type=_unwrap(a["type"]),
                                default=a.get("defaultValue")) for a in f["args"]]
                    obj.fields[f["name"]] = Field(parent=name, name=f["name"],
                                                  type=_unwrap(f["type"]), args=args)
                self.objects[name] = obj

    # ---- classification helpers -------------------------------------------

    def is_object(self, type_name: str) -> bool:
        o = self.objects.get(type_name)
        return o is not None and o.kind == "OBJECT"

    def is_leaf(self, type_name: str) -> bool:
        """A field type is a leaf if it is scalar or enum (no sub-selection)."""
        return type_name in self.scalars or type_name in self.enums

    def is_polymorphic(self, type_name: str) -> bool:
        o = self.objects.get(type_name)
        return o is not None and o.kind in ("INTERFACE", "UNION")

    def fanout_edges(self) -> list[Field]:
        """Every list-typed field: each is an edge that multiplies invocations.

        Includes both object-list edges (fire sub-resolvers) and scalar-list
        leaves. The caller distinguishes via ``is_leaf(edge.type.base)``.
        """
        edges = []
        for obj in self.objects.values():
            for f in obj.fields.values():
                if f.type.is_list:
                    edges.append(f)
        return edges

    def object_fanout_edges(self) -> list[Field]:
        """List fields returning an object/interface/union: true fanout."""
        return [e for e in self.fanout_edges() if not self.is_leaf(e.type.base)]

    def fragment_branches(self) -> list[tuple[str, str]]:
        """(abstract_type, concrete_type) pairs from interfaces/unions."""
        out = []
        for obj in self.objects.values():
            if obj.kind in ("INTERFACE", "UNION"):
                for pt in obj.possible_types:
                    out.append((obj.name, pt))
        return out

    def pagination_arg(self, f: Field) -> Arg | None:
        """Return the field's pagination-bearing arg, if any."""
        for pname in PAGINATION_ARGS:
            a = f.arg(pname)
            if a is not None:
                return a
        return None

    def root_fields(self) -> list[Field]:
        return list(self.objects[self.query_type].fields.values())

    def all_resolvers(self) -> list[Field]:
        """Every field on an OBJECT type is a resolver node."""
        out = []
        for obj in self.objects.values():
            if obj.kind == "OBJECT":
                out.extend(obj.fields.values())
        return out

    # ---- schema hash (Phase 4 preview; stable across runs) ----------------

    def schema_hash(self) -> str:
        canon = []
        for name in sorted(self.objects):
            obj = self.objects[name]
            for fn in sorted(obj.fields):
                f = obj.fields[fn]
                sig = f"{name}.{fn}:{f.type.base}[{f.type.list_depth}]" \
                      f"({','.join(sorted(a.name+':'+a.type.base for a in f.args))})"
                canon.append(sig)
        return hashlib.sha256("\n".join(canon).encode()).hexdigest()[:16]


def load_introspection(path: str) -> TypeGraph:
    with open(path) as fh:
        return TypeGraph(json.load(fh))


# The standard full GraphQL introspection query (verbatim output of
# graphql-core's get_introspection_query(), vendored so the engine has no
# graphql-core dependency). POST {"query": INTROSPECTION_QUERY} to an
# endpoint and feed the JSON response to TypeGraph.
INTROSPECTION_QUERY = """\
query IntrospectionQuery {
  __schema {

    queryType { name kind }
    mutationType { name kind }
    subscriptionType { name kind }
    types {
      ...FullType
    }
    directives {
      name
      description



      locations
      args {
        ...InputValue
      }
    }
  }
}

fragment FullType on __Type {
  kind
  name
  description


  fields(includeDeprecated: true) {
    name
    description
    args {
      ...InputValue
    }
    type {
      ...TypeRef
    }
    isDeprecated
    deprecationReason
  }
  inputFields {
    ...InputValue
  }
  interfaces {
    ...TypeRef
  }
  enumValues(includeDeprecated: true) {
    name
    description
    isDeprecated
    deprecationReason
  }
  possibleTypes {
    ...TypeRef
  }
}

fragment InputValue on __InputValue {
  name
  description
  type { ...TypeRef }
  defaultValue


}

fragment TypeRef on __Type {
  kind
  name
  ofType {
    name
    kind
    ofType {
      name
      kind
      ofType {
        name
        kind
        ofType {
          name
          kind
          ofType {
            name
            kind
            ofType {
              name
              kind
              ofType {
                name
                kind
                ofType {
                  name
                  kind
                  ofType {
                    name
                    kind
                  }
                }
              }
            }
          }
        }
      }
    }
  }
}
"""
