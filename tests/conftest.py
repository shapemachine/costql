import json
import os

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

PACKS = {
    "tmdb_t3": os.path.join(ROOT, "packs", "tmdb_t3.json"),
    "rickmorty_t1": os.path.join(ROOT, "packs", "rickmorty_t1.json"),
    "northwind_t1": os.path.join(ROOT, "packs", "northwind_t1.json"),
    "northwind_t2": os.path.join(ROOT, "packs", "northwind_t2.json"),
    "northwind_t3": os.path.join(ROOT, "packs", "northwind_t3.json"),
}


def pytest_addoption(parser):
    parser.addoption("--run-live", action="store_true", default=False,
                     help="run tests that hit a live demo server or public API")


def pytest_collection_modifyitems(config, items):
    if config.getoption("--run-live"):
        return
    skip = pytest.mark.skip(reason="live test: pass --run-live to enable")
    for item in items:
        if "live" in item.keywords:
            item.add_marker(skip)


@pytest.fixture(scope="session")
def root():
    return ROOT


@pytest.fixture(scope="session")
def packs():
    from costql import PricingPack
    return {name: PricingPack.load(path) for name, path in PACKS.items()}


@pytest.fixture(scope="session")
def oracle():
    with open(os.path.join(ROOT, "conformance", "quotes.json")) as fh:
        return json.load(fh)


@pytest.fixture(scope="session")
def contract_examples():
    with open(os.path.join(ROOT, "packs", "contract_examples.json")) as fh:
        return json.load(fh)
