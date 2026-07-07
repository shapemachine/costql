"""The regression backbone: every corpus quote must equal the frozen oracle
EXACTLY (same engine, same platform; quoting is deterministic float math on a
static pack). conformance/quotes.json doubles as the JS port's conformance
oracle (the JS side compares within the file's tolerance policy)."""
import json
import os

import pytest

from costql import PricingPack, validate

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
with open(os.path.join(ROOT, "conformance", "quotes.json")) as fh:
    _ORACLE = json.load(fh)["quotes"]

_pack_cache: dict = {}


def _pack(path: str) -> PricingPack:
    if path not in _pack_cache:
        _pack_cache[path] = PricingPack.load(os.path.join(ROOT, path))
    return _pack_cache[path]


@pytest.mark.parametrize("entry", _ORACLE,
                         ids=[f"{e['pack'].split('/')[-1]}::{e['query'][:40]}" for e in _ORACLE])
def test_quote_matches_oracle(entry):
    result = _pack(entry["pack"]).quote(entry["query"])
    assert result == entry["expected"]


@pytest.mark.parametrize("entry", _ORACLE,
                         ids=[f"{e['pack'].split('/')[-1]}::{e['query'][:40]}" for e in _ORACLE])
def test_quote_is_contract_valid(entry):
    assert validate(_pack(entry["pack"]).quote(entry["query"])) == []


def test_oracle_covers_all_committed_packs():
    packs = {e["pack"] for e in _ORACLE}
    assert packs == {"packs/tmdb_t3.json", "packs/rickmorty_t1.json",
                     "packs/northwind_t1.json", "packs/northwind_t2.json",
                     "packs/northwind_t3.json"}


def test_oracle_spans_the_interesting_cases():
    by_query = {(e["pack"], e["query"]): e["expected"] for e in _ORACLE}
    # a cyclic query is flagged low-confidence, never refused a price
    cyc = by_query[("packs/tmdb_t3.json",
                    '{ movie(id:"27205"){ recommendations{ recommendations{ title } } } }')]
    assert cyc["confidence"] == "low" and cyc["price"] > 0
    # a field that calls an outside host is named at T3 (host + call count, no fee)
    ai = by_query[("packs/tmdb_t3.json", '{ movie(id:"27205"){ aiSummary } }')]
    assert ai["external_calls"][0]["host"] == "api.anthropic.com"
    assert ai["external_calls"][0]["calls"] == 1
    # T1 results carry no breakdown/sharing (tier-gated)
    t1 = by_query[("packs/rickmorty_t1.json",
                   '{ character(id:"1"){ name status species gender } }')]
    assert "breakdown" not in t1 and "sharing" not in t1
    # the heavy-sharing hub query folds shared loaders at T3
    hub = by_query[("packs/northwind_t3.json",
                    '{ orders(first:20){ details(first:15){ product{ category{ name } } } } }')]
    assert any(s["loader"] == "categories" for s in hub["sharing"])
