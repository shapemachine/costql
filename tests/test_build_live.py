"""Live builds — opt in with `pytest --run-live`. Never run in PR CI.

The Rick & Morty build needs only the public internet (no keys). The TMDB build
needs the demo server up on :8000 with TMDB (and Anthropic) keys exported.
"""
import os

import pytest

pytestmark = pytest.mark.live

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_build_rickmorty_t1_end_to_end(tmp_path):
    from costql import build_pack, validate
    from costql.cli import _load_adapter
    factory = _load_adapter(os.path.join(ROOT, "examples", "adapters",
                                         "rickmorty.py") + ":rickmorty_config")
    pack = build_pack(factory(), repeats=2, verbose=False)
    assert pack.tier == "T1" and pack.currency == "wall_time_ms"
    out = tmp_path / "rm.json"
    pack.save(str(out))
    from costql import PricingPack
    q = PricingPack.load(str(out)).quote('{ character(id:"1"){ name } }')
    assert validate(q) == [] and q["price"] > 0


def test_build_tmdb_t3_end_to_end(tmp_path):
    from costql import build_pack, validate
    from costql.cli import _load_adapter
    factory = _load_adapter(os.path.join(ROOT, "examples", "adapters",
                                         "tmdb.py") + ":tmdb_config")
    cfg = factory(tier="T3")
    from costql.build import endpoint_up
    if not endpoint_up(cfg.graphql_url):
        pytest.skip("tmdb demo server not running on :8000")
    pack = build_pack(cfg, repeats=2, verbose=False)
    assert pack.tier == "T3" and pack.currency == "work_ms"
    assert pack.model.batch_groups                 # sharing observed
    q = pack.quote('{ movie(id:"27205"){ title } }')
    assert validate(q) == []
