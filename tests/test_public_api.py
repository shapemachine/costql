"""The frozen public surface, and the dependency posture: the quote path needs
numpy only: `requests` is a build-time extra and must never be required (or
even imported) to load a pack and quote."""
import subprocess
import sys

import costql


def test_all_names_exist():
    for name in costql.__all__:
        assert getattr(costql, name, None) is not None, name


def test_frozen_surface():
    assert set(costql.__all__) == {
        "__version__", "build_pack", "APIConfig", "ArgResolver", "InputSource",
        "MinedInputs", "UNSET", "CONTRACT_VERSION", "validate",
        "PricingPack", "PackVersionError"}


def test_import_and_quote_never_touch_requests(root):
    code = (
        "import sys\n"
        "sys.modules['requests'] = None\n"      # any `import requests` now fails
        "import costql\n"
        f"pack = costql.PricingPack.load({(root + '/packs/rickmorty_t1.json')!r})\n"
        "q = pack.quote('{ character(id:\"1\"){ name } }')\n"
        "assert q['price'] > 0\n"
        "print('ok')\n")
    out = subprocess.run([sys.executable, "-c", code],
                         capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    assert "ok" in out.stdout


def test_harness_gives_actionable_error_without_requests(monkeypatch):
    monkeypatch.setitem(sys.modules, "requests", None)
    from costql.harness import _requests
    try:
        _requests()
        raised = False
    except ImportError as e:
        raised = True
        assert "costql[build]" in str(e)
    assert raised


def test_version_matches_metadata():
    from importlib.metadata import version
    assert costql.__version__ == version("costql")
