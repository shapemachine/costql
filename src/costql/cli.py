"""The ``costql`` command line.

    costql probe https://rickandmortyapi.com/graphql
    costql build --adapter examples/adapters/tmdb.py:tmdb_config --out pack.json
    costql quote --pack pack.json '{ movie(id:"27205"){ title } }'
    costql validate --pack pack.json
    costql version

``probe`` checks a live endpoint before any adapter exists (achievable tier,
currency, where to harvest IDs). ``build`` calibrates a live API through an
adapter (see the adapter guide) and writes the pricing pack. ``quote`` and
``validate`` are fully offline: they never touch the network.
"""
from __future__ import annotations

import argparse
import importlib
import importlib.util
import json
import os
import sys
from collections.abc import Callable

from ._version import __version__
from .pack import PackVersionError, PricingPack


def _load_adapter(spec: str) -> Callable:
    """Resolve ``path/to/file.py:factory`` or ``dotted.module:factory`` to the
    adapter's config-factory callable."""
    target, sep, attr = spec.partition(":")
    if not sep or not attr:
        raise SystemExit(
            f"! --adapter must be FILE.py:factory or module.path:factory (got {spec!r})")
    if target.endswith(".py") or os.sep in target:
        if not os.path.exists(target):
            raise SystemExit(f"! adapter file not found: {target}")
        modspec = importlib.util.spec_from_file_location("_costql_adapter", target)
        module = importlib.util.module_from_spec(modspec)
        modspec.loader.exec_module(module)
    else:
        try:
            module = importlib.import_module(target)
        except ImportError as e:
            raise SystemExit(f"! cannot import adapter module {target!r}: {e}")
    fn = getattr(module, attr, None)
    if not callable(fn):
        raise SystemExit(f"! adapter has no callable {attr!r} (in {target})")
    return fn


def _load_pack(path: str) -> PricingPack:
    try:
        return PricingPack.load(path)
    except FileNotFoundError:
        raise SystemExit(f"! pack not found: {path}")
    except PackVersionError as e:
        raise SystemExit(f"! {e}")


def _resolve_pack(args: argparse.Namespace) -> PricingPack:
    """A quote's pack comes from either --pack <file> or --demo <name>."""
    if getattr(args, "demo", None):
        try:
            return PricingPack.demo(args.demo)
        except FileNotFoundError as e:
            raise SystemExit(f"! {e}")
    return _load_pack(args.pack)


def _render_quote(q: dict) -> str:
    typ = f"{q['typical_price']:.1f}" if q.get("typical_price") is not None else "n/a"
    lines = [f"  query      : {q['query'].strip()}",
             f"  tier/basis : {q['tier']} · {q['basis']}",
             f"  price      : {q['price']:.1f} {q['currency']}   (safe max, never under real cost)",
             f"  typical    : {typ} {q['currency']}   (typical everyday cost)",
             f"  confidence : {q['confidence']}"]
    if q["caveats"]:
        lines.append(f"  note       : {q['caveats'][0]}")
    top = sorted(q.get("breakdown") or [], key=lambda r: -r.get("cost", 0))[:6]
    if top:
        lines.append("  cost drivers (per-resolver breakdown):")
        for r in top:
            lines.append(f"      {r['resolver_id']:<28} {r.get('cost', 0):>8.1f}"
                         f"   (x{r.get('invocations', r.get('list_size', 1))})")
    for ec in q.get("external_calls") or []:
        rid = ec.get("resolver_id")
        who = f"{rid} -> {ec['host']}" if rid else ec["host"]
        lines.append(f"  external   : {who} "
                     f"(x{ec.get('calls', 1)} outside call(s); app prices these)")
    return "\n".join(lines)


def _cmd_build(args: argparse.Namespace) -> int:
    from .build import build_pack  # imports the measurement path (needs requests)
    factory = _load_adapter(args.adapter)
    config = factory(tier=args.tier) if args.tier else factory()
    pack = build_pack(config, repeats=args.repeats)
    out = args.out or f"pricing_pack_{config.name}.json"
    pack.save(out)
    size_kb = os.path.getsize(out) / 1024
    print(f"wrote {out} ({size_kb:.0f} KB): self-contained static reference; "
          f"consume it with:  costql quote --pack {out} '<query>'")
    return 0


def _cmd_probe(args: argparse.Namespace) -> int:
    from .probe import ProbeError, probe_endpoint, render_probe
    headers = {}
    for h in args.header:
        name, sep, value = h.partition(":")
        if not sep:
            raise SystemExit(f'! --header must be "Name: value" (got {h!r})')
        headers[name.strip()] = value.strip()
    try:
        report = probe_endpoint(args.url, headers=headers)
    except ProbeError as e:
        print(f"! {e}")
        return 1
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(render_probe(report))
    return 0


def _cmd_quote(args: argparse.Namespace) -> int:
    pack = _resolve_pack(args)
    result = pack.quote(args.query)
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print(f"pricing pack: schema {pack.schema_hash} · tier {pack.tier} · "
              f"currency {pack.currency} · offline\n")
        print(_render_quote(result))
    return 0


def _cmd_validate(args: argparse.Namespace) -> int:
    pack = _load_pack(args.pack)     # exercises pack_version + required-key checks
    problems = []
    if not pack.model.unit_cost:
        problems.append("model has no unit costs")
    try:
        roots = (pack.introspection.get("data", {}).get("__schema", {})
                 .get("queryType") or {})
        if not roots:
            problems.append("introspection has no query type")
    except AttributeError:
        problems.append("introspection section is malformed")
    print(f"pack       : {args.pack}")
    print(f"schema     : {pack.schema_hash}")
    print(f"tier       : {pack.tier} · currency {pack.currency}")
    print(f"model      : {len(pack.model.unit_cost)} resolver costs · "
          f"{len(pack.model.batch_groups)} shared-loader groups · "
          f"safety x{pack.model.safety}")
    ext = pack.model.external_hosts or {}
    print(f"external   : {len(ext)} observed outside-host call(s)")
    if problems:
        for p in problems:
            print(f"! {p}")
        return 1
    print("OK: readable, complete, quotable")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="costql",
        description="Price GraphQL queries before you run them.")
    sub = parser.add_subparsers(dest="command")

    p_build = sub.add_parser(
        "build", help="calibrate a live API through an adapter and write a pricing pack")
    p_build.add_argument("--adapter", required=True,
                         help="FILE.py:factory or dotted.module:factory returning an APIConfig")
    p_build.add_argument("--tier", choices=["T1", "T2", "T3"], default=None,
                         help="fidelity to request from the adapter (default: adapter's own)")
    p_build.add_argument("--repeats", type=int,
                         default=int(os.environ.get("COSTQL_REPEATS", "5")),
                         help="measurement rounds per query (default 5, env COSTQL_REPEATS)")
    p_build.add_argument("--out", default=None,
                         help="output pack path (default pricing_pack_<name>.json)")
    p_build.set_defaults(fn=_cmd_build)

    p_probe = sub.add_parser(
        "probe", help="check a live endpoint before writing an adapter: achievable "
                      "tier, currency, and where to harvest real IDs")
    p_probe.add_argument("url", help="the GraphQL endpoint URL")
    p_probe.add_argument("--header", action="append", default=[],
                         help='extra HTTP header as "Name: value" (repeatable)')
    p_probe.add_argument("--json", action="store_true",
                         help="print the probe report as JSON")
    p_probe.set_defaults(fn=_cmd_probe)

    p_quote = sub.add_parser(
        "quote", help="price a query from a pack: offline, no server, no network")
    src = p_quote.add_mutually_exclusive_group(required=True)
    src.add_argument("--pack", help="pricing pack JSON path")
    src.add_argument("--demo", metavar="NAME",
                     help="use a demo pack bundled in the package, e.g. tmdb_t3")
    p_quote.add_argument("--json", action="store_true",
                         help="print the raw contract result as JSON")
    p_quote.add_argument("query", help="the GraphQL query to price")
    p_quote.set_defaults(fn=_cmd_quote)

    p_val = sub.add_parser(
        "validate", help="check a pack is readable, complete, and version-compatible")
    p_val.add_argument("--pack", required=True, help="pricing pack JSON path")
    p_val.set_defaults(fn=_cmd_validate)

    p_ver = sub.add_parser("version", help="print the costql version")
    p_ver.set_defaults(fn=lambda a: (print(f"costql {__version__}"), 0)[1])
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "fn", None):
        parser.print_help()
        return 2
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
