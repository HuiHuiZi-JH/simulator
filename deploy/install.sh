#!/bin/sh
# MicroGrid Simulator -- target-side installer.
#
# Runs on the machine that will host the simulator. It expects to sit beside
# the files the release ships (microgridsim.pyz, config/, web/, the unit file)
# and does the four steps of the install procedure: put the unit file in
# /lib/systemd/system, reload, enable it for boot, and start it.
#
#   PREFIX=/opt/microgridsim   where the payload lands
#   SERVICE=MicroGridSimulator.service
#   UNIT_DIR=/lib/systemd/system   where the unit file goes
set -eu

HERE=$(cd "$(dirname "$0")" && pwd)
PREFIX=${PREFIX:-/opt/microgridsim}
SERVICE=${SERVICE:-MicroGridSimulator.service}
UNIT_DIR=${UNIT_DIR:-/lib/systemd/system}
[ -d "$UNIT_DIR" ] || UNIT_DIR=/etc/systemd/system

die() { echo "install: $*" >&2; exit 1; }

[ "$(id -u)" = 0 ] || die "run as root (sudo ./$(basename "$0"))"

# The archive holds compiled bytecode, which one Python minor version writes and
# only that same minor version can read. Checking here turns a crash loop under
# systemd into one clear sentence before anything is installed.
BUILT_FOR=$(cat "$HERE/PYTHON_VERSION" 2>/dev/null || echo unknown)
PYTHON=$(command -v python3 || true)
[ -n "$PYTHON" ] || die "python3 not found on this host"
HAVE=$("$PYTHON" -c 'import sys; print("%d.%d" % sys.version_info[:2])')
if [ "$BUILT_FOR" != unknown ] && [ "$BUILT_FOR" != "$HAVE" ]; then
    die "this release was built for Python $BUILT_FOR, but $PYTHON is $HAVE.
    Install python$BUILT_FOR on this host, or rebuild the release on a
    Python $HAVE machine."
fi

echo "Installing MicroGrid Simulator into $PREFIX (python3 $HAVE)"
mkdir -p "$PREFIX" "$PREFIX/log"

# The code. Root-readable only: nothing on this host needs to read it but the
# service, which runs as root.
install -m 0400 -o root -g root "$HERE/microgridsim.pyz" "$PREFIX/microgridsim.pyz"

# The dashboard is served to browsers, so it is never secret -- always take the
# version that shipped with this release.
rm -rf "$PREFIX/web"
cp -r "$HERE/web" "$PREFIX/web"
chmod -R go-rwx "$PREFIX/web"

# config/ is the operator's: device.json and any uploaded curve. Seed what is
# missing, keep what is already there, and say which files were left alone so an
# upgrade never silently reverts a site's settings.
mkdir -p "$PREFIX/config"
for f in "$HERE"/config/*; do
    [ -f "$f" ] || continue
    name=$(basename "$f")
    if [ -e "$PREFIX/config/$name" ]; then
        echo "  keeping existing config/$name"
    else
        install -m 0600 -o root -g root "$f" "$PREFIX/config/$name"
    fi
done
chmod 0700 "$PREFIX" "$PREFIX/config" "$PREFIX/log"

# 1. The unit file, with this install's paths written into it.
sed -e "s|@PREFIX@|$PREFIX|g" -e "s|@PYTHON@|$PYTHON|g" \
    "$HERE/$SERVICE" > "$UNIT_DIR/$SERVICE"
chmod 0644 "$UNIT_DIR/$SERVICE"

# 2. Pick up the new unit.
systemctl daemon-reload

# 3. Start at boot.
systemctl enable "$SERVICE"

# 4. Start now -- restart, so re-running this script upgrades a live host.
systemctl restart "$SERVICE"

sleep 2
systemctl status "$SERVICE" --no-pager || true

# The dashboard's port is device.json's to choose, and on a host where
# something already owns 8080 it will not be 8080. Report what this install will
# actually listen on rather than the default.
WEB_PORT=$("$PYTHON" -c "import json;print(json.load(open('$PREFIX/config/device.json')).get('web_port',8080))" 2>/dev/null || echo 8080)

cat <<MSG

MicroGrid Simulator installed.

  Modbus TCP      0.0.0.0:5021   (unit 1, address 0, quantity 2 = net kW x100)
  Web dashboard   http://<this-host>:$WEB_PORT
  Logs            $PREFIX/log/ and journalctl -u $SERVICE -f
  Settings        $PREFIX/config/device.json (or the dashboard's Config tab)

  Stop it with:   systemctl disable --now $SERVICE
MSG
