# Управление gateway server из выбранного при deploy окружения Python.
$ErrorActionPreference = 'Stop'
$GatewayPython = @@PYTHON@@
& $GatewayPython -m remote_watch.gateway_server --config (Join-Path $PSScriptRoot 'config/gateway_server.local.json') --@@ACTION@@
exit $LASTEXITCODE
