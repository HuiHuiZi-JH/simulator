#!/bin/sh
# MicroGrid Simulator -- build a release on the development machine.
#
#   ./deploy/build_release.sh [version]
#
# Produces one file, dist/microgridsim-<version>-py<X.Y>.run: a self-extracting
# installer holding the compiled simulator, the default config/, the dashboard,
# the systemd unit and install.sh. Copy that one file to a host, run it as root,
# and the service is installed, enabled and running.
#
# No .py file is in it. Every module is compiled to bytecode with docstrings
# stripped and the sources dropped, so the host has nothing to read. That hides
# the code from anyone looking at the machine; it is not encryption, and a
# determined reader with root can still decompile bytecode. Anything that must
# be secret does not belong on a host you do not control.
#
# Bytecode is tied to a Python minor version: the release runs on the same
# python3 minor as the machine that built it, and install.sh refuses a mismatch
# rather than leaving systemd to restart a broken service forever.
set -eu

ROOT=$(cd "$(dirname "$0")/.." && pwd)
cd "$ROOT"

PYVER=$(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])')
VERSION=${1:-$(git describe --always --dirty 2>/dev/null || echo dev)}
DIST="$ROOT/dist"
OUT="$DIST/microgridsim-$VERSION-py$PYVER.run"

STAGE=$(mktemp -d)
trap 'rm -rf "$STAGE"' EXIT INT TERM
CODE="$STAGE/code"        # becomes microgridsim.pyz
PAY="$STAGE/payload"      # becomes the .run
mkdir -p "$CODE" "$PAY" "$DIST"

echo "Building $VERSION for Python $PYVER"

# --- the code, compiled ------------------------------------------------------
# main.py is the archive's entry point, so it goes in under the name the zipapp
# runner looks for. config/ is split here: modbus_registers.py is code and
# belongs in the archive; the JSON and CSVs are the operator's and stay on disk.
cp main.py "$CODE/__main__.py"
cp -r src utils "$CODE/"
mkdir -p "$CODE/config"
cp config/modbus_registers.py "$CODE/config/"
find "$CODE" -name '__pycache__' -type d -prune -exec rm -rf {} +
find "$CODE" -name '.gitkeep' -delete

# A zip has no parent directory to fall back on, so every package in it is a
# real package with an __init__ rather than a namespace portion.
for d in config src src/models src/communication utils; do
    [ -f "$CODE/$d/__init__.py" ] || : > "$CODE/$d/__init__.py"
done

# -d rewrites the path baked into each .pyc, so a traceback on the host names
# the module and not the build machine's temporary directory.
python3 -m compileall -q -b -o 2 -d microgridsim "$CODE" >/dev/null
find "$CODE" -name '*.py' -delete
find "$CODE" -name '__pycache__' -type d -prune -exec rm -rf {} +

python3 - "$CODE" "$PAY/microgridsim.pyz" <<'PY'
import os, sys, zipfile
stage, out = sys.argv[1], sys.argv[2]
with open(out, 'wb') as f:
    f.write(b'#!/usr/bin/env python3\n')
    with zipfile.ZipFile(f, 'w', zipfile.ZIP_DEFLATED) as z:
        for root, dirs, files in os.walk(stage):
            dirs.sort()
            for name in sorted(files):
                p = os.path.join(root, name)
                z.write(p, os.path.relpath(p, stage))
os.chmod(out, 0o755)
PY

# --- the files the operator owns ---------------------------------------------
mkdir -p "$PAY/config"
for f in config/*; do
    case "$f" in
        *.py|*.pyc|*.bak|*/.gitkeep|*/__pycache__) continue ;;
    esac
    [ -f "$f" ] && cp "$f" "$PAY/config/"
done
cp -r web "$PAY/web"
cp deploy/install.sh deploy/MicroGridSimulator.service "$PAY/"
printf '%s\n' "$PYVER" > "$PAY/PYTHON_VERSION"
printf '%s\n' "$VERSION" > "$PAY/VERSION"
chmod +x "$PAY/install.sh"

# --- one self-extracting file ------------------------------------------------
cat > "$STAGE/head.sh" <<'HEADER'
#!/bin/sh
# MicroGrid Simulator installer. Run as root: sudo ./<this file>
# PREFIX=/some/where ./<this file>   installs somewhere other than /opt/microgridsim
set -eu
SKIP=$(awk '/^__PAYLOAD_BELOW__$/ { print NR + 1; exit 0 }' "$0")
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT INT TERM
tail -n +"$SKIP" "$0" | tar xzf - -C "$TMP"
PREFIX=${PREFIX:-/opt/microgridsim} SERVICE=${SERVICE:-MicroGridSimulator.service} \
    sh "$TMP/install.sh" "$@"
exit 0
__PAYLOAD_BELOW__
HEADER

tar czf "$STAGE/payload.tgz" -C "$PAY" .
cat "$STAGE/head.sh" "$STAGE/payload.tgz" > "$OUT"
chmod 0755 "$OUT"

echo
echo "Wrote $OUT ($(du -h "$OUT" | cut -f1))"
echo "Nothing in it but bytecode:"
python3 -c "import zipfile,sys; print('  ' + ', '.join(sorted(set(n.rsplit('.',1)[-1] for n in zipfile.ZipFile(sys.argv[1]).namelist()))))" "$PAY/microgridsim.pyz" 2>/dev/null || true
echo
echo "Deploy with:"
echo "  scp $(basename "$OUT") user@host:~/"
echo "  ssh user@host 'sudo ~/$(basename "$OUT")'"
