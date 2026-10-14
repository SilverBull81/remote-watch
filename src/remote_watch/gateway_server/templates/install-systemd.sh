#!/bin/sh
# Explicit root installation, no overwrite and no automatic process startup.
set -eu
unit=/etc/systemd/system/remote-watch-gateway.service
base=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
source="$base/remote-watch-gateway.service"
[ "$(id -u)" = 0 ] || { echo 'Root required'; exit 1; }
[ -f "$source" ] && [ ! -L "$source" ] || exit 1
[ ! -e "$unit" ] && [ ! -L "$unit" ] || { echo 'Unit already exists'; exit 1; }
if grep -q CHANGE_ME "$source"; then echo 'Set User/Group and check paths first'; exit 1; fi
systemd-analyze verify "$source"
# noclobber refuses a competing file creation; the system directory belongs to root.
(set -C; umask 022; cat "$source" > "$unit")
systemctl daemon-reload
systemctl enable remote-watch-gateway.service
echo 'Installed, not started. Use systemctl start remote-watch-gateway.service'
