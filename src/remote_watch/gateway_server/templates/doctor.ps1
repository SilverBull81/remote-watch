# Проверка deployment; --online и --report передаются явно.
$ErrorActionPreference = 'Stop'
$GatewayPython = @@PYTHON@@
& $GatewayPython -m remote_watch.diagnostics.gateway_doctor --mode server --config (Join-Path $PSScriptRoot 'gateway_server.local.json') @args
exit $LASTEXITCODE
