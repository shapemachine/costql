"""The bundled demo pack: a bare `pip install costql` must be able to run the
quickstart with no repo checkout, no file to fetch, and no network. That means
the demo pack ships inside the package and loads by name."""
import pytest

from costql import PricingPack


def test_demo_loads_by_name_and_quotes():
    pack = PricingPack.demo("tmdb_t3")
    q = pack.quote('{ movie(id:"27205"){ title } }')
    assert q["price"] > 0
    assert q["tier"] == "T3"


def test_demo_default_name_is_tmdb_t3():
    assert PricingPack.demo().schema_hash == PricingPack.demo("tmdb_t3").schema_hash


def test_unknown_demo_name_raises_filenotfound():
    with pytest.raises(FileNotFoundError):
        PricingPack.demo("does-not-exist")
