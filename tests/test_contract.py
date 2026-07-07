"""The frozen output contract v1.0: the 18 committed examples validate clean,
and the validator actually catches each class of violation."""
import copy

from costql import CONTRACT_VERSION, validate
from costql.contract import measured_result, predicted_result


def test_contract_version_is_frozen():
    assert CONTRACT_VERSION == "1.0"


def test_all_committed_examples_validate(contract_examples):
    assert contract_examples["contract_violations"] == 0
    for ex in contract_examples["examples"]:
        assert validate(ex["result"]) == [], ex["label"]


def _clean():
    return predicted_result(tier="T3", currency="work_ms", schema_hash="abc123",
                            price=10.0, typical_price=8.0, confidence="high",
                            caveats=[], breakdown=[], sharing=[], external_calls=[])


class _FakeExact:
    tier = "T2"
    currency = "work_ms"
    total = 12.5
    caveats: list = []
    resolvers: list = []
    loaders: list = []
    external_hosts: list = []


def test_assemblers_produce_valid_results():
    assert validate(_clean()) == []
    assert validate(measured_result(_FakeExact(), schema_hash="abc123")) == []


def test_price_is_mandatory_and_nonnegative():
    r = _clean(); del r["price"]
    assert validate(r)
    r = _clean(); r["price"] = -1.0
    assert validate(r)
    r = _clean(); r["price"] = "10"
    assert validate(r)


def test_tier_gating_is_enforced():
    # non-empty observed detail on a tier that can't have observed it = violation
    line = [{"resolver_id": "Q.x", "cost": 1.0, "invocations": 1}]
    share = [{"loader": "l", "folds": ["Q.x"], "counted_once": True}]
    r = _clean(); r["tier"] = "T1"; r["breakdown"] = line
    assert any("breakdown" in p for p in validate(r))
    r = _clean(); r["tier"] = "T2"; r["sharing"] = share
    assert any("sharing" in p for p in validate(r))
    r = _clean(); r["tier"] = "T3"; r["breakdown"] = line; r["sharing"] = share
    assert validate(r) == []
    # the assemblers silently drop sections a tier can't carry
    t1 = predicted_result(tier="T1", currency="work_ms", schema_hash="h",
                          price=1.0, typical_price=1.0, confidence="high",
                          caveats=[], breakdown=line, sharing=share,
                          external_calls=[])
    assert "breakdown" not in t1 and "sharing" not in t1


def test_unknown_enum_values_are_caught():
    for field, bogus in (("tier", "T4"), ("basis", "guessed"),
                         ("confidence", "certain"), ("currency", 7)):
        r = _clean()
        r[field] = bogus
        assert validate(r), field


def test_measured_result_is_exact_confidence():
    fake = _FakeExact()
    fake.total = 3.25
    m = measured_result(fake, schema_hash="abc123")
    assert m["confidence"] == "exact" and m["basis"] == "measured"
    assert m["price"] == m["typical_price"] == 3.25


def test_mutating_each_example_field_is_caught(contract_examples):
    ex = contract_examples["examples"][0]["result"]
    for field in ("contract_version", "tier", "basis", "currency", "price",
                  "confidence", "schema_hash", "caveats"):
        broken = copy.deepcopy(ex)
        del broken[field]
        assert validate(broken), f"removing {field} went unnoticed"
