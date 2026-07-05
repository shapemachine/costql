"""Pack persistence: round-trips, and the load-time version/shape gate."""
import copy
import json

import pytest

from costql import PackVersionError, PricingPack
from costql.pack import PACK_VERSION


def test_round_trip(packs, tmp_path):
    pack = packs["rickmorty_t1"]
    p = tmp_path / "rt.json"
    pack.save(str(p))
    again = PricingPack.load(str(p))
    assert again.to_dict() == pack.to_dict()
    q = '{ character(id:"1"){ name } }'
    assert again.quote(q) == pack.quote(q)


def test_from_dict_to_dict_round_trip(packs):
    d = packs["tmdb_t3"].to_dict()
    assert PricingPack.from_dict(copy.deepcopy(d)).to_dict() == d


def test_missing_pack_version_is_rejected(packs):
    d = packs["rickmorty_t1"].to_dict()
    del d["pack_version"]
    with pytest.raises(PackVersionError, match="pack_version"):
        PricingPack.from_dict(d)


def test_newer_pack_version_is_rejected(packs):
    d = packs["rickmorty_t1"].to_dict()
    d["pack_version"] = PACK_VERSION + 1
    with pytest.raises(PackVersionError, match="newer"):
        PricingPack.from_dict(d)


def test_unparseable_pack_version_is_rejected(packs):
    d = packs["rickmorty_t1"].to_dict()
    d["pack_version"] = "one"
    with pytest.raises(PackVersionError, match="unreadable"):
        PricingPack.from_dict(d)


@pytest.mark.parametrize("key", ["schema_hash", "introspection", "model"])
def test_missing_required_section_is_rejected(packs, key):
    d = packs["rickmorty_t1"].to_dict()
    del d[key]
    with pytest.raises(PackVersionError, match=key):
        PricingPack.from_dict(d)


def test_not_a_pack_at_all():
    with pytest.raises(PackVersionError):
        PricingPack.from_dict({"hello": "world"})


def test_saved_file_is_stable_json(packs, tmp_path):
    p = tmp_path / "p.json"
    packs["northwind_t3"].save(str(p))
    d = json.loads(p.read_text())
    assert d["pack_version"] == PACK_VERSION
    assert set(d) >= {"schema_hash", "tier", "currency", "introspection",
                      "model", "adjustments"}


def test_authored_fee_folds_into_price(packs):
    pack = packs["tmdb_t3"]
    q = '{ movie(id:"27205"){ aiSummary } }'
    base = pack.quote(q)["price"]
    d = pack.to_dict()
    d["adjustments"] = {"adjustments": {"Movie.aiSummary": {
        "added_unit_cost": 100.0, "downstream_host": "api.anthropic.com"}}}
    fee_quote = PricingPack.from_dict(d).quote(q)
    assert fee_quote["price"] > base + 50     # the authored fee raises the ceiling
    assert fee_quote["external_costs"][0]["authored_fee"] == 100.0
