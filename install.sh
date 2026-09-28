#!/bin/sh
# Install the fisean command.
#
#   ./install.sh                       # into ~/bin
#   BIN=/usr/local/bin ./install.sh    # elsewhere; a system path needs sudo
#
# It symlinks one dispatcher into $BIN. Everything else stays in this directory,
# so that is the install: a symlink to a lone copy of fisean.py would find no
# static/ directory and no browse.py beside it, and would say so rather than
# half-working. Re-running this script is safe; it is idempotent.

set -eu

here=$(cd "$(dirname "$0")" && pwd)
bin=${BIN:-$HOME/bin}
name=fisean

for needed in fisean.py browse.py static/index.html static/app.css static/app.js; do
    if [ ! -e "$here/$needed" ]; then
        echo "install.sh: $needed is missing" >&2
        echo "install.sh: this does not look like a complete fisean checkout" >&2
        exit 1
    fi
done

if ! command -v python3 >/dev/null 2>&1; then
    echo "install.sh: python3 is not on PATH" >&2
    exit 1
fi

if [ ! -d "$bin" ]; then
    if ! mkdir -p "$bin" 2>/dev/null; then
        echo "install.sh: cannot create $bin" >&2
        echo "install.sh: for a system location try: sudo BIN=$bin $here/install.sh" >&2
        exit 1
    fi
fi

if [ ! -w "$bin" ]; then
    echo "install.sh: $bin is not writable by you" >&2
    echo "install.sh: for a system location try: sudo BIN=$bin $here/install.sh" >&2
    exit 1
fi

# The shebang and the exec bit are the whole of the packaging.
chmod +x "$here/fisean.py" "$here/browse.py"
ln -sf "$here/fisean.py" "$bin/$name"

echo "installed $bin/$name -> $here/fisean.py"

# Prove the install works rather than assuming it: this dispatches to browse.py,
# which is the part a bare symlink would break. Quiet on success, loud on failure.
if ! "$bin/$name" browse --help >/dev/null; then
    echo "install.sh: $bin/$name is installed but does not run" >&2
    exit 1
fi
echo "and it runs: $("$bin/$name" help | head -1)"

case ":$PATH:" in
    *":$bin:"*)
        ;;
    *)
        echo
        echo "$bin is not on your PATH. Add this line to ~/.zshrc:"
        echo
        echo "    export PATH=\"$bin:\$PATH\""
        ;;
esac
