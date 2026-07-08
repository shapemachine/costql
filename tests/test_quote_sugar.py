"""Sugar invariance: fragments, aliases, variables, and directives change how
a query is WRITTEN, never what the server does, so the sugared spelling must
price EXACTLY like its hand-flattened equivalent (same contract result, minus
the echoed query text). Polymorphic branches are the one exception: they add
work the flat form can't express, so they get a caveat instead (see
test_union_branches below)."""
import pytest

from costql import PricingPack


@pytest.fixture(scope="module")
def tmdb(packs):
    return packs["tmdb_t3"]


def _same_quote(pack, sugared: str, flat: str, variables: dict | None = None):
    a = pack.quote(sugared, variables)
    b = pack.quote(flat)
    a.pop("query")
    b.pop("query")
    assert a == b


def test_named_fragment_prices_like_flat(tmdb):
    _same_quote(
        tmdb,
        'fragment F on Movie { title genres{ name } } { movie(id:"27205"){ ...F } }',
        '{ movie(id:"27205"){ title genres{ name } } }')


def test_inline_fragment_prices_like_flat(tmdb):
    _same_quote(
        tmdb,
        '{ movie(id:"27205"){ ... on Movie { cast(limit:8){ person{ name } } } } }',
        '{ movie(id:"27205"){ cast(limit:8){ person{ name } } } }')


def test_alias_prices_like_flat(tmdb):
    _same_quote(
        tmdb,
        '{ hit: movie(id:"27205"){ title } }',
        '{ movie(id:"27205"){ title } }')


def test_duplicate_aliases_price_twice_not_once(tmdb):
    once = tmdb.quote('{ movie(id:"27205"){ title } }')
    twice = tmdb.quote('{ a: movie(id:"27205"){ title } b: movie(id:"155"){ title } }')
    assert twice["price"] > once["price"]


def test_variables_price_like_inline_literals(tmdb):
    _same_quote(
        tmdb,
        'query($n: Int!){ movie(id:"27205"){ cast(limit:$n){ person{ name } } } }',
        '{ movie(id:"27205"){ cast(limit:4){ person{ name } } } }',
        variables={"n": 4})


def test_variable_default_prices_like_inline_literal(tmdb):
    _same_quote(
        tmdb,
        'query($n: Int = 8){ movie(id:"27205"){ cast(limit:$n){ person{ name } } } }',
        '{ movie(id:"27205"){ cast(limit:8){ person{ name } } } }')


def test_missing_variable_prices_like_undeclared_size(tmdb):
    # No value, no default -> the arg drops, pricing the field like a query
    # that never declared a size (ceiling's worst-case bound; never under).
    _same_quote(
        tmdb,
        'query($n: Int){ movie(id:"27205"){ cast(limit:$n){ person{ name } } } }',
        '{ movie(id:"27205"){ cast{ person{ name } } } }')


def test_directives_price_as_included(tmdb):
    _same_quote(
        tmdb,
        '{ movie(id:"27205"){ title @include(if:true) genres @skip(if:false){ name } } }',
        '{ movie(id:"27205"){ title genres{ name } } }')


# ---- polymorphic branches (union schema; synthetic conformance pack) --------

@pytest.fixture(scope="module")
def union_pack(root):
    return PricingPack.load(f"{root}/conformance/union_pack.json")


def test_union_branches_all_priced_with_caveat(union_pack):
    both = union_pack.quote(
        '{ search(query:"a", first:5){ ... on Movie { title similar(first:2)'
        '{ title } } ... on Book { title } } }')
    assert any("polymorphic branches" in c for c in both["caveats"])
    assert any(b["resolver_id"] == "Movie.similar" for b in both["breakdown"])
    # dropping a branch can only lower the price (sum over branches = ceiling)
    movie_only = union_pack.quote(
        '{ search(query:"a", first:5){ ... on Movie { title similar(first:2)'
        '{ title } } } }')
    assert movie_only["price"] <= both["price"]


def test_same_type_fragment_carries_no_branch_caveat(tmdb):
    q = tmdb.quote('{ movie(id:"27205"){ ... on Movie { title } } }')
    assert not any("polymorphic branches" in c for c in q["caveats"])
