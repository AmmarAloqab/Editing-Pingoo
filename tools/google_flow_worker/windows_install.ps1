$ErrorActionPreference = "Stop"

$RepoDir = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$BaseDir = if ($env:PINGOO_FLOW_BASE_DIR) { $env:PINGOO_FLOW_BASE_DIR } else { Join-Path $env:LOCALAPPDATA "PingooGoogleFlow" }
$VenvDir = Join-Path $BaseDir "venv"

New-Item -ItemType Directory -Force -Path $BaseDir | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $BaseDir "profile") | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $BaseDir "downloads") | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $BaseDir "logs") | Out-Null

py -3 -m venv $VenvDir
& (Join-Path $VenvDir "Scripts\python.exe") -m pip install --upgrade pip
& (Join-Path $VenvDir "Scripts\python.exe") -m pip install -r (Join-Path $RepoDir "tools\google_flow_worker\requirements.txt")

Write-Output "WINDOWS_WORKER_READY=PASS"
Write-Output "AUTH_PROFILE=$(Join-Path $BaseDir 'profile')"
Write-Output "NEXT_AUTH_COMMAND=powershell -ExecutionPolicy Bypass -File .\tools\google_flow_worker\windows_auth.ps1"
