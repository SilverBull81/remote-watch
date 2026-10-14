# Start only the interactive controller, never a second manager.
$ErrorActionPreference = 'Stop'
$GatewayPython = @@PYTHON@@
$GatewayConfig = Join-Path $PSScriptRoot 'gateway_server.local.json'
Start-Process -FilePath $GatewayPython -ArgumentList @('-I', '-B', '-m', 'remote_watch.gateway_server.service', '--config', ('"' + $GatewayConfig + '"'), '--tray') -WindowStyle Hidden
