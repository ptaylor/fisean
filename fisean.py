#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 Paul Taylor
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
    "scan": ("scan.py", "index", None),
    "index": ("scan.py", "index", None),
    "label": ("label.py", "label", "fisean-labels"),
    "open": ("browse.py", "browse", None),
    "browse": ("browse.py", "browse", None),
}

# The labelling half is the only command with dependencies, and they cannot live
# in the interpreter this project otherwise runs on: PyTorch's last Intel-macOS
# wheel is 2.2.2, and it stops at Python 3.12. So `fisean label` execs the virtual
# environment's own interpreter. FISEAN_LABEL_PYTHON overrides where that is.
LABEL_PYTHON = Path(os.environ.get("FISEAN_LABEL_PYTHON") or
                    Path.home() / ".venvs" / "fisean-labels" / "bin" / "python")

LABEL_INSTALL = """fisean label needs PyTorch and open_clip, which live in their own environment:

    python3.12 -m venv ~/.venvs/fisean-labels
    ~/.venvs/fisean-labels/bin/pip install "torch==2.2.2" "numpy<2" open_clip_torch pyyaml
"""

# The directory name `fisean scan` writes by default, and the one `fisean browse`
# looks for under a library root. The same constant appears in scan.py and
# browse.py: the halves are separate programs and either may be run on its own,
# so neither may import the other.
INDEX_DIR_NAME = "fisean-index"

USAGE = """usage: fisean <command> [args]

commands:
  scan <DIR>      index every video under DIR, so the browser can read it
  index <DIR>     the same command, under its other name
  label <DIR>     ask a model what is happening in each video, for the "who & what" filter
  open <DIR>      serve the index in DIR and open it in a browser
  browse <DIR>    the same command, for when "browse" reads better

<DIR> is either a library root or the index directory itself. A library root is
searched for a 'fisean-index' subdirectory, which is where scan puts the index
unless told otherwise; point at the index directory itself, under any name, to use
one kept somewhere else. With no <DIR>, scan indexes the current directory and
the browser opens the committed fixture.

Anything after <DIR> is passed straight to the command:

  fisean scan ~/Videos --force --jobs 4
  fisean label ~/Videos --limit 20 --calibrate
  fisean browse ~/Videos --port 9000
  fisean scan --help

label is the one command with dependencies: it needs PyTorch and open_clip, which
are installed in their own virtual environment because PyTorch has no wheel for the
interpreter the rest of the project uses. Run it with that environment's python if
the note it prints is more use than this line.
"""


def usage(stream) -> None:
    stream.write(USAGE)


def fail(message: str) -> int:
    sys.stderr.write(f"fisean: {message}\n")
    sys.stderr.write("try 'fisean help'\n")
    return 2


def find_index(given: str) -> Path | None:
    """The index directory for a path given on the command line.

    Accepts the index directory itself - under any name, anywhere - or a library
    root containing one. Only the conventional name is searched for under a root;
    an index called something else is reached by naming it.
    """
    path = Path(given).expanduser()
    if (path / "manifest.json").is_file():
        return path
    conventional = path / INDEX_DIR_NAME
    if (conventional / "manifest.json").is_file():
        return conventional
    return None


def main(argv: list[str]) -> int:
    if not argv:
        usage(sys.stderr)
        return 2
    if argv[0] in ("-h", "--help", "help"):
        usage(sys.stdout)
        return 0

    command, *rest = argv

    if command not in COMMANDS:
        return fail(f"unknown command '{command}'")
    program, mode, environment = COMMANDS[command]

    forwarded: list[str] = []
    if rest and not rest[0].startswith("-"):
        given, *rest = rest
        if not Path(given).expanduser().exists():
            return fail(f"no such directory: {given}")
        if mode == "browse":
            # Browsing needs an index that is already there, and says which
            # command would have made one rather than failing obscurely.
            index = find_index(given)
            if index is None:
                return fail(f"no index under '{given}' — run 'fisean scan {given}' first, "
                            f"or point at the index directory itself")
            forwarded += ["--index", str(index)]
        else:
            # Scanning is handed the directory as it was given: it resolves the
            # index itself, and may be creating it for the first time.
            forwarded.append(given)
    forwarded += rest

    target = HERE / program
    if not target.is_file():
        return fail(f"{program} is missing from {HERE} — the install is incomplete")

    interpreter = sys.executable
    if environment:
        # A command with dependencies runs under its own interpreter, and says how
        # to build that environment rather than failing with "no such file".
        interpreter = str(LABEL_PYTHON if environment == "fisean-labels" else environment)
        if not Path(interpreter).exists():
            sys.stderr.write(LABEL_INSTALL)
            sys.stderr.write(f"\nlooked for: {interpreter}\n")
            return 1

    # execv, not subprocess: no extra process, no signal forwarding to get wrong,
    # and the exit code is the command's own.
    os.execv(interpreter, [interpreter, str(target), *forwarded])
    return 0  # unreachable while execv succeeds


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
