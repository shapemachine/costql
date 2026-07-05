"""Anti-rot check: every `costql ...` command line in docs/, skills/, README,
and examples must parse against the REAL argparse tree. Catches prose drifting
from the CLI (a renamed flag, a removed subcommand) at CI time.

    python scripts/check_cli_docs.py
"""
from __future__ import annotations

import os
import re
import shlex
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from costql.cli import build_parser  # noqa: E402

SCAN = ["README.md", "CONTRIBUTING.md", "AGENTS.md", "docs", "skills", "examples"]
CMD = re.compile(r"^\s*(?:\$\s*)?costql\s+(.*)$")
PLACEHOLDER = re.compile(r"<[^>]+>|\.\.\.|…")


def files():
    for entry in SCAN:
        p = os.path.join(ROOT, entry)
        if os.path.isfile(p):
            yield p
        else:
            for dirpath, _dirs, names in os.walk(p):
                for n in names:
                    if n.endswith(".md") or n.endswith(".py"):
                        yield os.path.join(dirpath, n)


def check_line(parser, rest: str) -> str | None:
    """Return an error string if the costql command line is invalid."""
    # normalize: strip trailing shell continuations/comments, placeholder args
    rest = rest.split("#")[0].strip().rstrip("\\").strip()
    if not rest or rest.startswith("--help") or rest == "…":
        return None
    cleaned = PLACEHOLDER.sub("PLACEHOLDER", rest)
    try:
        argv = shlex.split(cleaned)
    except ValueError:
        return None            # half a line of prose, not a command
    if not argv or argv[0] not in ("build", "quote", "validate", "version"):
        return f"unknown subcommand in: costql {rest}"
    try:
        parser.parse_args(argv)
    except SystemExit as e:
        if e.code not in (0, None):
            return f"does not parse: costql {rest}"
    return None


def main() -> int:
    parser = build_parser()
    # argparse prints to stderr on failure; keep CI output clean
    parser.error = lambda msg: (_ for _ in ()).throw(SystemExit(2))  # type: ignore[assignment]
    for a in parser._subparsers._group_actions[0].choices.values():  # type: ignore[union-attr]
        a.error = lambda msg: (_ for _ in ()).throw(SystemExit(2))  # type: ignore[assignment]

    problems = []
    n = 0
    for path in files():
        with open(path, encoding="utf-8") as fh:
            lines = fh.readlines()
        i = 0
        while i < len(lines):
            m = CMD.match(lines[i])
            lineno = i + 1
            i += 1
            if not m:
                continue
            cmd = m.group(1)
            # join backslash-continued lines into one command
            while cmd.rstrip().endswith("\\") and i < len(lines):
                cmd = cmd.rstrip().rstrip("\\") + " " + lines[i].strip()
                i += 1
            n += 1
            err = check_line(parser, cmd)
            if err:
                problems.append(f"{os.path.relpath(path, ROOT)}:{lineno}: {err}")
    if problems:
        print("\n".join(problems))
        print(f"\n{len(problems)} stale costql command line(s) out of {n} checked")
        return 1
    print(f"all {n} documented costql command lines parse against the current CLI")
    return 0


if __name__ == "__main__":
    sys.exit(main())
