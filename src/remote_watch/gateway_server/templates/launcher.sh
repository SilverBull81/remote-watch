#!/bin/sh
# Управление gateway server; пути не зависят от текущей рабочей папки.
set -eu
gateway_directory=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
exec @@PYTHON@@ -m remote_watch.gateway_server --config "$gateway_directory/gateway_server.local.json" --@@ACTION@@
