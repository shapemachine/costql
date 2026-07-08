"""Generate conformance/union_pack.json: a tiny SYNTHETIC pack whose schema
has a union (Query.search -> [SearchResult] -> Movie | Book). None of the
committed demo packs has a polymorphic type, so this pack is what lets the
conformance suite pin the fragment-branch pricing semantics (every branch
walked, caveat attached) identically across the Python and JS engines.

The costs are hand-authored, not measured: this pack exists to freeze parser
and pricing BEHAVIOR, not to demonstrate calibration.

    python scripts/make_union_pack.py
"""
from __future__ import annotations

import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from costql.introspect import TypeGraph  # noqa: E402


def _named(kind: str, name: str) -> dict:
    return {"kind": kind, "name": name, "ofType": None}


def _list_of(inner: dict) -> dict:
    return {"kind": "LIST", "name": None, "ofType": inner}


def _field(name: str, ftype: dict, args: list | None = None) -> dict:
    return {"name": name, "args": args or [], "type": ftype,
            "isDeprecated": False, "deprecationReason": None}


def _arg(name: str, atype: dict) -> dict:
    return {"name": name, "type": atype, "defaultValue": None}


def _obj(name: str, fields: list) -> dict:
    return {"kind": "OBJECT", "name": name, "fields": fields,
            "possibleTypes": None, "enumValues": None}


def _scalar(name: str) -> dict:
    return {"kind": "SCALAR", "name": name, "fields": None,
            "possibleTypes": None, "enumValues": None}


STRING = _named("SCALAR", "String")
INT = _named("SCALAR", "Int")

INTROSPECTION = {"data": {"__schema": {
    "queryType": {"name": "Query"},
    "mutationType": None,
    "subscriptionType": None,
    "types": [
        _obj("Query", [
            _field("search", _list_of(_named("UNION", "SearchResult")),
                   [_arg("query", STRING), _arg("first", INT)]),
        ]),
        {"kind": "UNION", "name": "SearchResult", "fields": None,
         "possibleTypes": [{"kind": "OBJECT", "name": "Movie", "ofType": None},
                           {"kind": "OBJECT", "name": "Book", "ofType": None}],
         "enumValues": None},
        _obj("Movie", [
            _field("title", STRING),
            _field("runtime", INT),
            _field("similar", _list_of(_named("OBJECT", "Movie")),
                   [_arg("first", INT)]),
        ]),
        _obj("Book", [
            _field("title", STRING),
            _field("pages", INT),
        ]),
        _scalar("ID"), _scalar("String"), _scalar("Int"), _scalar("Boolean"),
    ],
}}}


def main() -> None:
    schema_hash = TypeGraph(INTROSPECTION).schema_hash()
    model = {
        "schema_hash": schema_hash,
        "cost_currency": "wall_time_ms",
        "unit_cost": {"Query.search": 2.0, "Movie.similar": 1.0,
                      "__doc_fields__": 0.05},
        "safety": 1.25,
        "default_cap": 50,
        "noise_buffer_ms": 0.0,
        "batch_groups": {},
        "loader_fns": {},
        "max_size": {"Query.search": 10, "Movie.similar": 5},
        "typical_size": {"Query.search": 4, "Movie.similar": 2},
        "uncovered_edges": [],
        "scan_before_paginate": [],
        "external_hosts": {},
    }
    pack = {
        "pack_version": 1,
        "schema_hash": schema_hash,
        "currency": "wall_time_ms",
        "tier": "T3",
        "note": ("SYNTHETIC conformance pack: hand-authored costs over a "
                 "union schema, freezing fragment-branch pricing behavior "
                 "across engines. Not a calibrated demo."),
        "introspection": INTROSPECTION,
        "model": model,
    }
    dest = os.path.join(ROOT, "conformance", "union_pack.json")
    with open(dest, "w") as fh:
        json.dump(pack, fh, indent=2, sort_keys=True)
        fh.write("\n")
    print(f"wrote {dest}: schema {schema_hash}")


if __name__ == "__main__":
    main()
