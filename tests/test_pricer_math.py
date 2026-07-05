"""The fit and the pricing math on synthetic, known-answer data."""
from costql.introspect import TypeGraph
from costql.pricer import DOC_FEATURE, CostModelBuilder, Pricer
from costql.query_ir import parse_query


def _tg(packs):
    return TypeGraph(packs["rickmorty_t1"].introspection)


def test_builder_recovers_linear_unit_costs(packs):
    tg = _tg(packs)
    r1, r2 = "Query.character", "Query.characters"
    calib = [({r1: a, r2: b, DOC_FEATURE: 0}, 10.0 * a + 5.0 * b)
             for a in (1, 2, 3) for b in (1, 2)]
    m = CostModelBuilder(tg, default_cap=20).build(
        calib, uncovered_edges=[], max_size={}, typical_size={})
    assert abs(m.unit_cost[r1] - 10.0) < 0.5
    assert abs(m.unit_cost[r2] - 5.0) < 0.5


def test_nnls_never_fits_negative_costs(packs):
    tg = _tg(packs)
    r1, r2 = "Query.character", "Query.characters"
    # r2's presence *reduces* total work in this adversarial data; NNLS must
    # clamp it at zero rather than emit a negative unit cost.
    calib = [({r1: 1, r2: 0, DOC_FEATURE: 0}, 10.0),
             ({r1: 1, r2: 1, DOC_FEATURE: 0}, 8.0),
             ({r1: 2, r2: 0, DOC_FEATURE: 0}, 20.0),
             ({r1: 2, r2: 2, DOC_FEATURE: 0}, 16.0)]
    m = CostModelBuilder(tg, default_cap=20).build(
        calib, uncovered_edges=[], max_size={}, typical_size={})
    assert all(c >= 0 for c in m.unit_cost.values())


def test_safety_covers_every_calibration_row(packs):
    pack = packs["rickmorty_t1"]
    assert pack.model.safety >= 1.0


def test_ceiling_dominates_expectation(packs):
    for name, pack in packs.items():
        tg = TypeGraph(pack.introspection)
        pricer = Pricer(tg, pack.model)
        for q in (f'{{ {f} }}' for f in _first_roots(tg, 2)):
            sels = parse_query(q)
            ceil = pricer.price(sels, mode="ceiling", fold=True).score
            typ = pricer.price(sels, mode="expectation", fold=True).score
            assert ceil >= typ - 1e-9, (name, q)


def _first_roots(tg, n):
    out = []
    for rf in tg.root_fields():
        if not rf.args or all(not a.required for a in rf.args):
            leaf = _first_leaf(tg, rf)
            if leaf:
                out.append(f"{rf.name}{{ {leaf} }}" if not tg.is_leaf(rf.type.base)
                           else rf.name)
        if len(out) >= n:
            break
    return out


def _first_leaf(tg, rf):
    obj = tg.objects.get(rf.type.base)
    if obj is None:
        return None
    for f in obj.fields.values():
        if tg.is_leaf(f.type.base):
            return f.name
    return None


def test_quote_price_scales_with_declared_size(packs):
    # a wider declared page multiplies child-resolver invocations; the folded
    # loader's RISING batch curve then prices the wider read higher
    pack = packs["northwind_t3"]
    small = pack.quote('{ orders(first:5){ details(first:5){ quantity } } }')
    big = pack.quote('{ orders(first:40){ details(first:5){ quantity } } }')
    assert big["price"] > small["price"] * 2


def test_folded_flat_loader_does_not_scale(packs):
    # TMDB's per-id HTTP loaders have FLAT curves: a wider cast page folds onto
    # the same one-call-per-id credits read, so the ceiling stays put. This is
    # the sharing model working, not a bug.
    pack = packs["tmdb_t3"]
    small = pack.quote('{ movie(id:"27205"){ cast(limit:2){ person{ name } } } }')
    big = pack.quote('{ movie(id:"27205"){ cast(limit:12){ person{ name } } } }')
    assert big["price"] == small["price"]


def test_unknown_field_still_returns_a_price(packs):
    # the contract: never refuse to price
    q = packs["rickmorty_t1"].quote('{ character(id:"1"){ name notAField } }')
    assert q["price"] >= 0
