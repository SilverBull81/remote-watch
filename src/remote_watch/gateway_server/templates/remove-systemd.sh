#!/bin/sh
# Remove only the exact stopped unit; runtime data and Caddy remain untouched.
set -eu
unit=/etc/systemd/system/remote-watch-gateway.service
base=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
source="$base/remote-watch-gateway.service"
[ "$(id -u)" = 0 ] || { echo 'Root required'; exit 1; }
[ -f "$unit" ] && [ ! -L "$unit" ] && [ -f "$source" ] && [ ! -L "$source" ] || exit 1
cmp -s "$source" "$unit" || { echo 'Different unit: refusing removal'; exit 1; }
state=$(systemctl show --property=ActiveState --value remote-watch-gateway.service)
[ "$state" = inactive ] || [ "$state" = failed ] || { echo 'Stop unit first'; exit 1; }
systemctl disable remote-watch-gateway.service
rm -- "$unit"
systemctl daemon-reload
