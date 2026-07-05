"""The confidence classifier: cyclic recursion and data-dependent sizes are
flagged (priced as a ceiling, confidence downgraded), never silently billed."""


def test_cyclic_recursion_is_low_confidence(packs):
    q = packs["tmdb_t3"].quote(
        '{ movie(id:"27205"){ recommendations{ recommendations{ title } } } }')
    assert q["confidence"] == "low"
    assert any("cyclic" in c for c in q["caveats"])
    assert q["price"] > 0                     # flagged, still priced


def test_cross_type_cycle_is_low_confidence(packs):
    q = packs["rickmorty_t1"].quote(
        '{ character(id:"1"){ episode{ characters{ name } } } }')
    assert q["confidence"] == "low"


def test_predictable_shapes_are_high_confidence(packs):
    for pack, query in (
            ("tmdb_t3", '{ movie(id:"27205"){ cast(limit:8){ person{ name } } } }'),
            ("rickmorty_t1", '{ character(id:"1"){ name status species gender } }'),
            ("northwind_t3", '{ product(id:"1"){ name unitPrice category{ name } } }')):
        q = packs[pack].quote(query)
        assert q["confidence"] == "high", (pack, query, q["caveats"])


def test_caveat_text_tells_the_user_what_to_do(packs):
    q = packs["tmdb_t3"].quote(
        '{ movie(id:"27205"){ similar{ similar{ title } } } }')
    assert any("run it" in c or "upper bound" in c for c in q["caveats"])
