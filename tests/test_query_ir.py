"""The query IR: parsing, serialization, and the v0.2 query surface
(aliases resolved, fragments expanded, variables substituted, directives
priced as included, lenient tokenizer)."""
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


def test_list_and_object_literal_args():
    sels = parse_query('{ orders(ids:[1, 2, 3], filter:{country:"MX", min:2}){ id } }')
    assert sels[0].args == {"ids": [1, 2, 3], "filter": {"country": "MX", "min": 2}}


def test_known_limitation_lenient_tokenizer():
    # An unbalanced query does not raise; it parses what it can.
    sels = parse_query('{ movie(id:"1"){ title }')
    assert sels[0].name == "movie"


# ---- aliases ---------------------------------------------------------------

def test_alias_resolves_to_field_name():
    sels = parse_query('{ hit: movie(id:"1"){ title } }')
    assert sels[0].name == "movie" and sels[0].args == {"id": "1"}


def test_duplicate_aliases_price_the_field_twice():
    sels = parse_query('{ a: movie(id:"1"){ title } b: movie(id:"2"){ title } }')
    assert [s.name for s in sels] == ["movie", "movie"]
    assert count_fields(sels) == 4


# ---- fragments ---------------------------------------------------------------

def test_named_fragment_expands_at_spread_site():
    sels = parse_query(
        'fragment F on Movie { title genres{ name } } { movie(id:"1"){ ...F } }')
    frag = sels[0].children[0]
    assert frag.on == "Movie" and frag.name == "on:Movie"
    assert [c.name for c in frag.children] == ["title", "genres"]


def test_fragment_definition_may_follow_the_operation():
    sels = parse_query('{ movie(id:"1"){ ...F } } fragment F on Movie { title }')
    assert sels[0].children[0].on == "Movie"


def test_inline_fragment():
    sels = parse_query('{ movie(id:"1"){ ... on Movie { title } } }')
    frag = sels[0].children[0]
    assert frag.on == "Movie" and [c.name for c in frag.children] == ["title"]


def test_conditionless_inline_fragment_splices():
    sels = parse_query('{ movie(id:"1"){ ... { title } } }')
    assert [c.name for c in sels[0].children] == ["title"]


def test_unknown_fragment_raises():
    with pytest.raises(ValueError, match="unknown fragment"):
        parse_query('{ movie(id:"1"){ ...Missing } }')


def test_fragment_cycle_raises():
    with pytest.raises(ValueError, match="fragment cycle"):
        parse_query('fragment A on Movie { ...B } fragment B on Movie { ...A } '
                    '{ movie(id:"1"){ ...A } }')


def test_multiple_operations_raise():
    with pytest.raises(ValueError, match="multiple operations"):
        parse_query('{ a { x } } { b { y } }')


def test_fragment_nodes_do_not_count_as_doc_fields():
    sugared = parse_query('{ movie(id:"1"){ ... on Movie { title } } }')
    flat = parse_query('{ movie(id:"1"){ title } }')
    assert count_fields(sugared) == count_fields(flat) == 2


def test_fragment_serialize_round_trips():
    q = '{ search(query:"a"){ ... on Movie { title } } }'
    assert parse_query(serialize(parse_query(q))) == parse_query(q)


# ---- variables ---------------------------------------------------------------

def test_variable_substitution():
    sels = parse_query('query($n: Int!){ cast(limit:$n){ name } }', {"n": 4})
    assert sels[0].args == {"limit": 4}


def test_variable_default_applies_when_not_provided():
    sels = parse_query('query($n: Int = 8){ cast(limit:$n){ name } }')
    assert sels[0].args == {"limit": 8}


def test_provided_value_beats_default():
    sels = parse_query('query($n: Int = 8){ cast(limit:$n){ name } }', {"n": 2})
    assert sels[0].args == {"limit": 2}


def test_missing_variable_drops_the_argument():
    # No value, no default -> the arg vanishes, so the ceiling's worst-case
    # bound applies to that field (never under-prices).
    sels = parse_query('query($n: Int){ cast(limit:$n){ name } }')
    assert sels[0].args == {}


def test_variable_inside_list_literal():
    sels = parse_query('query($a: Int){ orders(ids:[$a, 2]){ id } }', {"a": 1})
    assert sels[0].args == {"ids": [1, 2]}


def test_list_type_variable_definition_parses():
    sels = parse_query('query($ids: [ID!]!){ orders(ids:$ids){ id } }', {"ids": [1]})
    assert sels[0].args == {"ids": [1]}


# ---- directives ----------------------------------------------------------------

def test_directives_are_priced_as_included():
    sels = parse_query(
        'query($yes: Boolean = true){ movie(id:"1") @include(if:$yes){ '
        'title @skip(if:false) } }')
    assert sels[0].name == "movie"
    assert [c.name for c in sels[0].children] == ["title"]
