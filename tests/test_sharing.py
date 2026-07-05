"""The sharing model: coalescing observed in traces maps resolvers onto shared
loaders (T3's fold-to-once, then batch-curve, pricing)."""
from costql.sharing import build_sharing_model


def _obs(loader_stats, invocations):
    return {"op": "q", "trace": {"loaders": loader_stats, "invocations": invocations},
            "invocations": invocations}


def test_coalesced_loader_is_grouped():
    sm = build_sharing_model([_obs(
        {"categories": {"requested_keys": 10, "batch_calls": 1, "cache_hits": 0}},
        {"Product.category": 10})], tg=None, known_loaders=["categories"])
    assert sm.batch_group.get("Product.category") == "categories"


def test_uncorrelated_resolver_is_not_grouped():
    # A.x tracks the loader's requested_keys across ops; B.y does not — only
    # the correlated resolver is attributed to the loader.
    obs = [
        {"op": "q1", "trace": {"loaders": {"cat": {"requested_keys": 10, "batch_calls": 1}},
                               "invocations": {"A.x": 10, "B.y": 3}},
         "invocations": {"A.x": 10, "B.y": 3}},
        {"op": "q2", "trace": {"loaders": {"cat": {"requested_keys": 4, "batch_calls": 1}},
                               "invocations": {"A.x": 4, "B.y": 3}},
         "invocations": {"A.x": 4, "B.y": 3}},
    ]
    sm = build_sharing_model(obs, tg=None, known_loaders=["cat"])
    assert sm.batch_group.get("A.x") == "cat"
    assert "B.y" not in sm.batch_group


def test_declared_but_never_fired_loader_is_dead():
    sm = build_sharing_model([_obs(
        {"categories": {"requested_keys": 10, "batch_calls": 1, "cache_hits": 0}},
        {"Product.category": 10})], tg=None, known_loaders=["categories", "ghost"])
    assert "ghost" in sm.dead_loaders


def test_committed_t3_packs_learned_real_groups(packs):
    nw = packs["northwind_t3"].model.batch_groups
    assert nw.get("Product.category") == "categories"
    assert nw.get("OrderDetail.product") == "products"
    tmdb = packs["tmdb_t3"].model.batch_groups
    assert tmdb.get("Credit.person") == "/person/{id}"


def test_t1_packs_have_no_groups(packs):
    assert packs["rickmorty_t1"].model.batch_groups == {}
    assert packs["northwind_t1"].model.batch_groups == {}
