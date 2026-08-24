$ErrorActionPreference = "Stop"

$RepoDir = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$BaseDir = if ($env:PINGOO_FLOW_BASE_DIR) { $env:PINGOO_FLOW_BASE_DIR } else { Join-Path $env:LOCALAPPDATA "PingooGoogleFlow" }

if (-not $env:PINGOO_UPLOAD_URL) {
  Write-Error "Set PINGOO_UPLOAD_URL to the VPS Editing-Pingoo API URL reachable over Tailscale, for example http://100.x.y.z:18080/api/v1/video_materials"
}

if (-not $env:PINGOO_FLOW_WORKER_HOST) {
  Write-Output "PINGOO_FLOW_WORKER_HOST not set. Defaulting to 127.0.0.1 for local-only test. Use Windows Tailscale IP for VPS access."
}

$env:PYTHONPATH = $RepoDir
$env:PINGOO_FLOW_BASE_DIR = $BaseDir

& (Join-Path $BaseDir "venv\Scripts\python.exe") -m tools.google_flow_worker.worker
