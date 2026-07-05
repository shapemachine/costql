"""The costql CLI: offline subcommands end-to-end, and the error paths."""
import json
import os

import pytest

from costql.cli import main

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PACK = os.path.join(ROOT, "packs", "rickmorty_t1.json")


def test_version(capsys):
    assert main(["version"]) == 0
    assert "costql" in capsys.readouterr().out


def test_no_args_prints_help(capsys):
    assert main([]) == 2
    assert "build" in capsys.readouterr().out


def test_quote_human(capsys):
    assert main(["quote", "--pack", PACK, '{ character(id:"1"){ name } }']) == 0
    out = capsys.readouterr().out
    assert "price" in out and "wall_time_ms" in out and "ceiling" in out


def test_quote_json_is_contract_valid(capsys):
    from costql import validate
    assert main(["quote", "--pack", PACK, "--json",
                 '{ character(id:"1"){ name } }']) == 0
    result = json.loads(capsys.readouterr().out)
    assert validate(result) == []


def test_quote_missing_pack_exits_cleanly():
    with pytest.raises(SystemExit, match="not found"):
        main(["quote", "--pack", "nope.json", "{ x }"])


def test_validate_ok(capsys):
    assert main(["validate", "--pack", PACK]) == 0
    assert "OK" in capsys.readouterr().out


def test_validate_rejects_newer_pack(tmp_path):
    d = json.load(open(PACK))
    d["pack_version"] = 99
    p = tmp_path / "future.json"
    p.write_text(json.dumps(d))
    with pytest.raises(SystemExit, match="newer"):
        main(["validate", "--pack", str(p)])


def test_build_rejects_bad_adapter_spec():
    with pytest.raises(SystemExit, match="adapter"):
        main(["build", "--adapter", "nocolon"])
    with pytest.raises(SystemExit, match="not found"):
        main(["build", "--adapter", "missing_file.py:cfg"])


def test_build_adapter_loading_resolves_the_reference_adapters():
    from costql.cli import _load_adapter
    fn = _load_adapter(os.path.join(ROOT, "examples", "adapters",
                                    "rickmorty.py") + ":rickmorty_config")
    cfg = fn()
    assert cfg.name == "rickmorty" and cfg.tier == "T1"
    assert callable(cfg.calibration_queries)
    assert len(cfg.calibration_queries("whale")) >= 5
