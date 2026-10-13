#!/bin/sh
# Проверка deployment; сеть включается только явным --online.
set -eu
gateway_directory=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
exec @@PYTHON@@ -m remote_watch.diagnostics.gateway_doctor --mode server --config "$gateway_directory/gateway_server.local.json" "$@"
