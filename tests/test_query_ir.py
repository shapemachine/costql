"""The query IR: parsing, serialization, and the documented v0.1 limitations
(no fragments; aliases are not resolved; the tokenizer is lenient)."""
import pytest

from costql.query_ir import count_fields, parse_query, serialize


def test_basic_shape():
    sels = parse_query('{ movie(id:"27205"){ title genres{ name } } }')
    assert len(sels) == 1
    root = sels[0]
    assert root.name == "movie" and root.args == {"id": "27205"}
    assert [c.name for c in root.children] == ["title", "genres"]


def test_count_fields():
    assert count_fields(parse_query('{ movie(id:"1"){ title genres{ name } } }')) == 4


def test_multiple_roots():
    sels = parse_query('{ categories{ name } orders(first:5){ orderDate } }')
    assert [s.name for s in sels] == ["categories", "orders"]
    assert sels[1].args == {"first": 5}


def test_operation_keyword_and_name():
    sels = parse_query('query Foo { character(id:"1"){ name } }')
    assert sels[0].name == "character"


def test_serialize_round_trips_structure():
    q = '{ order(id:"15000"){ details(first:15){ quantity product{ name } } } }'
    assert parse_query(serialize(parse_query(q))) == parse_query(q)


def test_int_float_bool_args():
    sels = parse_query('{ orders(first:40, refunded:false, minTotal:1.5){ id } }')
    assert sels[0].args == {"first": 40, "refunded": False, "minTotal": 1.5}


def test_fragments_are_rejected():
    with pytest.raises(ValueError):
        parse_query('fragment F on Movie { title } { movie(id:"1"){ ...F } }')


def test_known_limitation_aliases_not_resolved():
    # v0.1: an alias becomes the selection name (the field it aliases is lost).
    # Frozen here so the JS port matches and a future fix is a deliberate change.
    sels = parse_query('{ hit: movie(id:"1"){ title } }')
    assert sels[0].name == "hit"


def test_known_limitation_lenient_tokenizer():
    # An unbalanced query does not raise; it parses what it can.
    sels = parse_query('{ movie(id:"1"){ title }')
    assert sels[0].name == "movie"
