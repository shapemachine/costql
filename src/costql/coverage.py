"""Coverage Query Generator.

Generates a *set* of GraphQL operations that together fire every resolver and
every fanout edge at least once. One DB-hitting root field per operation when
config.one_root_per_op is set (some servers cannot run sibling DB roots
concurrently on a shared session).

Depth is forced via a repetition budget: each object type may be expanded a
bounded number of times along a path, which drives depth without unbounded
blowup on recursive types.

Auth is resolved per-op from config: a field may require stricter auth than its
root. We pick the op credential that satisfies the strictest *reachable* field,
and drop any field the chosen credential cannot satisfy (recording it so union
coverage can confirm it is fired by some other op). All auth and
uncoverable-field knowledge comes from the APIConfig: nothing schema-specific
lives here.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .config import APIConfig
from .inputs import ArgProvider
from .introspect import Field, TypeGraph
from .query_ir import Selection, serialize


def _satisfies(cred: str | None, req: str | None) -> bool:
    if req is None:
        return True
    if req == "user_or_service":
        return cred in ("user", "service")
    return cred == req


@dataclass
class CoverageOp:
    root_field: str
    auth: str | None
    selection: Selection | None
    query: str
    fired_resolvers: set[str] = field(default_factory=set)
    fired_edges: set[str] = field(default_factory=set)
    dropped_fields: set[str] = field(default_factory=set)
    deferred_bounded: set[str] = field(default_factory=set)   # sampled in isolation, not here


class CoverageGenerator:
    def __init__(self, tg: TypeGraph, args: ArgProvider, config: APIConfig,
                 max_depth: int = 12, type_budget: int = 2):
        self.tg = tg
        self.args = args
        self.config = config
        self.max_depth = max_depth
        self.type_budget = type_budget

    def _req_auth(self, resolver_id: str) -> str | None:
        return self.config.field_auth.get(resolver_id)

    def _reachable_types(self, root_field: Field) -> set[str]:
        seen: set[str] = set()
        stack = [root_field.type.base]
        while stack:
            t = stack.pop()
            if t in seen or self.tg.is_leaf(t):
                continue
            seen.add(t)
            obj = self.tg.objects.get(t)
            if obj:
                for f in obj.fields.values():
                    stack.append(f.type.base)
        return seen

    def _choose_credential(self, root_field: Field) -> str | None:
        root_req = self.config.root_auth.get(root_field.name)
        reachable = self._reachable_types(root_field)
        reqs = {self._req_auth(f"{t}.{fn}")
                for t in reachable
                for fn in (self.tg.objects[t].fields if t in self.tg.objects else {})
                if f"{t}.{fn}" not in self.config.uncoverable_fields}
        reqs.discard(None)
        service_only = "service" in reqs
        user_only = "user" in reqs
        # Root demands a specific credential -> use it.
        if root_req == "user":
            return "user"
        if root_req == "service":
            return "service"
        # Public or user_or_service root: elevate to cover stricter descendants.
        if service_only and not user_only:
            return "service"
        if user_only and not service_only:
            return "user"
        if root_req == "user_or_service":
            return "service"
        return None  # public root, no special descendants

    def _field_args(self, path: str, f: Field, size: str) -> dict:
        out = {}
        pag = self.tg.pagination_arg(f)
        for a in f.args:
            if a.required or (pag is not None and a.name == pag.name):
                out[a.name] = self.args.value(path, a.name, a.type.base, size)
        return out

    def _expand(self, f: Field, path: str, depth: int, type_counts: dict[str, int],
                op: CoverageOp, size: str, cred: str | None) -> Selection | None:
        # Drop fields that cannot execute against the live backend.
        if f.resolver_id in self.config.uncoverable_fields:
            op.dropped_fields.add(f.resolver_id)
            return None
        # Bounded fields (e.g. a paid external call) are kept OUT of the
        # combinatorial panel: fanning them out multiplies real cost with no
        # modelling gain: and sampled once in isolation (build_isolation).
        if f.resolver_id in self.config.bounded_fields:
            op.deferred_bounded.add(f.resolver_id)
            return None
        # Field-level auth gate: drop fields the op credential can't satisfy.
        if not _satisfies(cred, self._req_auth(f.resolver_id)):
            op.dropped_fields.add(f.resolver_id)
            return None

        base = f.type.base
        op.fired_resolvers.add(f.resolver_id)
        if f.type.is_list:
            op.fired_edges.add(f.resolver_id)

        if self.tg.is_leaf(base) or base not in self.tg.objects:
            return Selection(name=f.name, args=self._field_args(path, f, size))

        obj = self.tg.objects[base]
        if depth >= self.max_depth or type_counts.get(base, 0) >= self.type_budget:
            return None

        tc = dict(type_counts)
        tc[base] = tc.get(base, 0) + 1
        children: list[Selection] = []
        for child in obj.fields.values():
            if self.tg.is_leaf(child.type.base):
                c = self._expand(child, f"{path}.{child.name}", depth + 1, tc, op, size, cred)
                if c is not None:
                    children.append(c)
        for child in obj.fields.values():
            if not self.tg.is_leaf(child.type.base):
                c = self._expand(child, f"{path}.{child.name}", depth + 1, tc, op, size, cred)
                if c is not None:
                    children.append(c)
        if not children:
            return None
        return Selection(name=f.name, args=self._field_args(path, f, size), children=children)

    def build_isolation(self) -> list[CoverageOp]:
        """One minimal op per bounded field, firing it exactly ONCE via a
        single-entity root: so a paid/external resolver is sampled at its true
        per-invocation cost without any fanout multiplication. Generic: for a
        bounded resolver ``Type.field`` we find a non-list root field whose base
        type is ``Type`` and select just that field under it."""
        ops: list[CoverageOp] = []
        for rid in self.config.bounded_fields:
            parent_type, _, fname = rid.partition(".")
            root = self._single_entity_root(parent_type)
            if root is None:
                continue                     # no single-entity path -> can't isolate
            child = self.tg.objects[parent_type].fields.get(fname)
            if child is None:
                continue
            cred = self._choose_credential(root)
            leaf = Selection(name=fname, args=self._field_args(f"{root.name}.{fname}", child, "small"))
            sel = Selection(name=root.name,
                            args=self._field_args(root.name, root, "small"),
                            children=[leaf])
            op = CoverageOp(root_field=root.name, auth=cred, selection=sel,
                            query=serialize([sel], op_name=f"isolate_{fname}"))
            op.fired_resolvers.add(rid)
            ops.append(op)
        return ops

    def _single_entity_root(self, parent_type: str) -> Field | None:
        """A root field returning a single (non-list) instance of ``parent_type``."""
        for rf in self.tg.root_fields():
            if rf.type.base == parent_type and not rf.type.is_list:
                return rf
        return None

    def build_all(self, size: str = "whale") -> list[CoverageOp]:
        ops = []
        for rf in self.tg.root_fields():
            cred = self._choose_credential(rf)
            op = CoverageOp(root_field=rf.name, auth=cred, selection=None, query="")
            sel = self._expand(rf, rf.name, 0, {}, op, size, cred)
            if sel is None:
                continue
            op.selection = sel
            op.query = serialize([sel], op_name=f"cover_{rf.name}")
            ops.append(op)
        return ops
