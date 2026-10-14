# Explicit service operation; registration never starts the gateway automatically.
$ErrorActionPreference = 'Stop'
$GatewayPython = @@PYTHON@@
& $GatewayPython -I -B -m remote_watch.gateway_server.service --config (Join-Path $PSScriptRoot 'gateway_server.local.json') @@ACTION@@ @args
exit $LASTEXITCODE
