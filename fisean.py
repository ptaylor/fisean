#!/usr/bin/env python3
"""fisean — one entry point for the fisean tools.

    fisean open <DIR>      serve the index in DIR and open it in a browser
    fisean browse <DIR>    the same command; both spellings are kept because both
                           read naturally at a prompt
    fisean index <DIR>     index a library — not implemented yet

<DIR> is either the index directory itself (the one holding `manifest.json`) or
a library root with an `index/` subdirectory in it. Both are accepted, so
neither has to be remembered. With no DIR at all the committed fixture is used,
which is what makes this repository runnable from a fresh clone.

This is a **dispatcher, not a wrapper**: it replaces itself with the program for
the subcommand, so signals, exit codes and output behave exactly as if that
program had been run directly. That also keeps the two halves separate programs,
which AGENTS.md requires — the entry point must not grow into a third
implementation of either.

Install it with `./install.sh`; see that script for where it goes.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# resolve() follows a symlink, so an installed ~/bin/fisean still finds the
# checkout it points at. Nothing else in here may depend on the current directory.
HERE = Path(__file__).resolve().parent

COMMANDS = {
    "open": "browse.py",
    "browse": "browse.py",
}

# Kept visible rather than hidden, so the shape of the tool is discoverable and
# the absence is stated rather than implied by a missing command.
NOT_IMPLEMENTED = {
    "index": "the indexer does not exist yet — see AGENTS.md and docs/index-format.md",
}

USAGE = """usage: fisean <command> [args]

commands:
  open <DIR>      serve the index in DIR and open it in a browser
  browse <DIR>    the same command, for when "browse" reads better
  index <DIR>     not implemented yet

<DIR> may be the index directory itself (holding manifest.json) or a library
root with an index/ subdirectory in it. With no DIR, the committed fixture is
used.

Anything after <DIR> is passed straight to the command:

  fisean browse ~/Library/Application\\ Support/videos/holidays --port 9000
  fisean open . --no-open
  fisean browse --help
"""


def usage(stream) -> None:
    stream.write(USAGE)


def fail(message: str) -> int:
    sys.stderr.write(f"fisean: {message}\n")
    sys.stderr.write("try 'fisean help'\n")
    return 2


def find_index(given: str) -> Path | None:
    """The index directory for a path given on the command line.

    Accepts the index directory or a root containing one, so `fisean open .`
    works whether you are standing in the index or one level above it.
    """
    path = Path(given).expanduser()
    for candidate in (path, path / "index"):
        if (candidate / "manifest.json").is_file():
            return candidate
    return None


def main(argv: list[str]) -> int:
    if not argv:
        usage(sys.stderr)
        return 2
    if argv[0] in ("-h", "--help", "help"):
        usage(sys.stdout)
        return 0

    command, *rest = argv

    if command in NOT_IMPLEMENTED:
        sys.stderr.write(f"fisean: {command}: {NOT_IMPLEMENTED[command]}\n")
        return 1
    if command not in COMMANDS:
        return fail(f"unknown command '{command}'")

    forwarded: list[str] = []
    if rest and not rest[0].startswith("-"):
        given, *rest = rest
        if not Path(given).expanduser().exists():
            return fail(f"no such directory: {given}")
        index = find_index(given)
        if index is None:
            return fail(f"no index in '{given}' or '{given}/index' "
                        f"(looking for a manifest.json)")
        forwarded += ["--index", str(index)]
    forwarded += rest

    target = HERE / COMMANDS[command]
    if not target.is_file():
        return fail(f"{COMMANDS[command]} is missing from {HERE} — the install is incomplete")

    # execv, not subprocess: no extra process, no signal forwarding to get wrong,
    # and the exit code is the command's own.
    os.execv(sys.executable, [sys.executable, str(target), *forwarded])
    return 0  # unreachable while execv succeeds


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
