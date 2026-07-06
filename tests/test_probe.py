"""`costql probe`: tier/currency classification from cost_trace, id-source
discovery from introspection, and the error paths. All network is injected."""
import json
import os

import pytest

from costql.probe import ProbeError, probe_endpoint, render_probe

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
URL = "https://api.example/graphql"

with open(os.path.join(ROOT, "packs", "rickmorty_t1.json")) as fh:
    INTROSPECTION = json.load(fh)["introspection"]


def fake_post(trace):
    """A post() that answers introspection, then a trivial query with `trace`."""
    def post(url, payload, headers, timeout):
        if "__schema" in payload["query"]:
            return INTROSPECTION
        resp = {"data": {"__typename": "Query"}}
        if trace is not None:
            resp["extensions"] = {"cost_trace": trace}
        return resp
    return post


def test_no_trace_is_t1_wall_clock():
    r = probe_endpoint(URL, post=fake_post(None))
    assert r["tier"] == "T1"
    assert r["cost_currency"] == "wall_time_ms"
    assert any("re-probe" in n for n in r["notes"])


def test_work_only_trace_is_t2():
    r = probe_endpoint(URL, post=fake_post({"work_ms": 3.2, "invocations": {"Query.__typename": 1}}))
    assert r["tier"] == "T2"
    assert r["cost_currency"] == "work_ms"


def test_loaders_plus_work_is_t3():
    trace = {"work_ms": 3.2, "loaders": {"/person/{id}": {"requested_keys": 1}}}
    r = probe_endpoint(URL, post=fake_post(trace))
    assert r["tier"] == "T3"
    assert r["cost_currency"] == "work_ms"


def test_loaders_without_work_stays_t1_with_note():
    r = probe_endpoint(URL, post=fake_post({"loaders": {"x": {}}}))
    assert r["tier"] == "T1"
    assert any("work_ms" in n for n in r["notes"])


def test_id_sources_found_without_curated_ids():
    r = probe_endpoint(URL, post=fake_post(None))
    paths = {s["path"] for s in r["id_sources"]}
    # Rick & Morty: connection-style roots reach id-bearing entities one hop in
    assert "characters.results" in paths
    src = next(s for s in r["id_sources"] if s["path"] == "characters.results")
    assert src["entity"] == "Character"
    assert src["pagination_arg"] == "page"
    # roots that REQUIRE arguments (character(id:)) are not id sources
    assert not any(s["path"].startswith("character.") or s["path"] == "character"
                   for s in r["id_sources"])


def test_render_is_human_and_actionable():
    out = render_probe(probe_endpoint(URL, post=fake_post(None)))
    assert "tier today : T1" in out
    assert "wall_time_ms" in out
    assert "characters" in out
    assert "skills/costql-adapter" in out


def test_unreachable_raises_probe_error():
    def down(url, payload, headers, timeout):
        raise ConnectionError("boom")
    with pytest.raises(ProbeError, match="unreachable"):
        probe_endpoint(URL, post=down)


def test_no_schema_raises_probe_error():
    def no_schema(url, payload, headers, timeout):
        return {"errors": [{"message": "introspection is disabled"}]}
    with pytest.raises(ProbeError, match="introspection"):
        probe_endpoint(URL, post=no_schema)


def test_cli_probe_json(monkeypatch, capsys):
    import costql.probe as probe_mod
    from costql.cli import main
    monkeypatch.setattr(probe_mod, "_default_post", fake_post(None))
    # the CLI passes probe_endpoint's default post argument at call time, so
    # patch probe_endpoint itself to use the injected transport
    real = probe_mod.probe_endpoint
    monkeypatch.setattr(
        probe_mod, "probe_endpoint",
        lambda url, headers=None, timeout=30: real(url, headers, timeout, post=fake_post(None)))
    assert main(["probe", URL, "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["tier"] == "T1"


def test_cli_probe_bad_header():
    from costql.cli import main
    with pytest.raises(SystemExit):
        main(["probe", URL, "--header", "not-a-header"])
