$ErrorActionPreference = "Stop"

$RepoDir = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$BaseDir = if ($env:PINGOO_FLOW_BASE_DIR) { $env:PINGOO_FLOW_BASE_DIR } else { Join-Path $env:LOCALAPPDATA "PingooGoogleFlow" }
$env:PYTHONPATH = $RepoDir
$env:PINGOO_FLOW_BASE_DIR = $BaseDir

& (Join-Path $BaseDir "venv\Scripts\python.exe") -m tools.google_flow_worker.auth_flow
