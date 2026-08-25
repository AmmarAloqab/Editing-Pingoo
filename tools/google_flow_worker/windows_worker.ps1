param(
  [string]$UploadUrl = "",
  [string]$WorkerHost = "",
  [string]$WorkerPort = "8767",
  [string]$Headless = "false"
)

$ErrorActionPreference = "Stop"

$RepoDir = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$BaseDir = if ($env:PINGOO_FLOW_BASE_DIR) { $env:PINGOO_FLOW_BASE_DIR } else { Join-Path $env:LOCALAPPDATA "PingooGoogleFlow" }

if ($UploadUrl) { $env:PINGOO_UPLOAD_URL = $UploadUrl }
if ($WorkerHost) { $env:PINGOO_FLOW_WORKER_HOST = $WorkerHost }
if ($WorkerPort) { $env:PINGOO_FLOW_WORKER_PORT = $WorkerPort }
if ($Headless) { $env:PINGOO_FLOW_HEADLESS = $Headless }

if (-not $env:PINGOO_UPLOAD_URL) {
  Write-Error "Set PINGOO_UPLOAD_URL to the VPS Editing-Pingoo API URL reachable over Tailscale, for example http://100.x.y.z:18080/api/v1/video_materials"
}

if (-not $env:PINGOO_FLOW_WORKER_HOST) {
  Write-Output "PINGOO_FLOW_WORKER_HOST not set. Defaulting to 127.0.0.1 for local-only test. Use Windows Tailscale IP for VPS access."
}

$env:PYTHONPATH = $RepoDir
$env:PINGOO_FLOW_BASE_DIR = $BaseDir
if (-not $env:PINGOO_FLOW_HEADLESS) {
  $env:PINGOO_FLOW_HEADLESS = "false"
}

& (Join-Path $BaseDir "venv\Scripts\python.exe") -m tools.google_flow_worker.worker
