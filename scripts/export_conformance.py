"""Freeze the conformance oracle: run every corpus query through the Python
engine and write the full contract results to conformance/quotes.json.

    python scripts/export_conformance.py

The Python golden tests (tests/test_quote_golden.py) assert EXACT equality
against this file; the JS port's conformance suite asserts deep equality with
the numeric tolerance policy (see conformance/README.md). Regenerate only when
an intentional engine change shifts prices — the diff is the review artifact.
"""
from __future__ import annotations

import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from costql import PricingPack, validate  # noqa: E402


def main() -> None:
    with open(os.path.join(ROOT, "conformance", "queries.json")) as fh:
        corpus = json.load(fh)

    out, violations = [], 0
    for pack_path, queries in corpus.items():
        if pack_path.startswith("_"):
            continue
        pack = PricingPack.load(os.path.join(ROOT, pack_path))
        for q in queries:
            result = pack.quote(q)
            errs = validate(result)
            if errs:
                violations += 1
                print(f"! contract violation for {pack_path} :: {q}: {errs}")
            out.append({"pack": pack_path, "query": q, "expected": result})

    if violations:
        sys.exit(f"! {violations} contract violations — not writing the oracle")
    dest = os.path.join(ROOT, "conformance", "quotes.json")
    with open(dest, "w") as fh:
        json.dump({"tolerance": {"relative": 1e-9, "absolute": 1e-6,
                                 "_comment": "JS port: numbers must match within "
                                 "max(absolute, relative*|expected|); all non-numeric "
                                 "fields must be deep-equal"},
                   "quotes": out}, fh, indent=2, sort_keys=True)
    print(f"wrote {dest}: {len(out)} frozen quotes, 0 contract violations")


if __name__ == "__main__":
    main()
