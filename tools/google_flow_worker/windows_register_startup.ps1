param(
  [string]$WorkerHost = "100.123.55.125",
  [string]$UploadUrl = "http://100.104.63.125:18080/api/v1/video_materials",
  [string]$WorkerPort = "8767",
  [string]$TaskName = "Pingoo Google Flow Worker"
)

$ErrorActionPreference = "Stop"

$WorkerScript = (Resolve-Path (Join-Path $PSScriptRoot "windows_worker.ps1")).Path
$PowerShell = (Get-Command powershell.exe).Source
$Arguments = @(
  "-NoProfile"
  "-WindowStyle Hidden"
  "-ExecutionPolicy Bypass"
  "-File `"$WorkerScript`""
  "-UploadUrl `"$UploadUrl`""
  "-WorkerHost `"$WorkerHost`""
  "-WorkerPort `"$WorkerPort`""
  "-Headless `"false`""
) -join " "

$Action = New-ScheduledTaskAction -Execute $PowerShell -Argument $Arguments
$Trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$Settings = New-ScheduledTaskSettingsSet `
  -RestartCount 10 `
  -RestartInterval (New-TimeSpan -Minutes 1) `
  -ExecutionTimeLimit (New-TimeSpan -Days 3650) `
  -StartWhenAvailable `
  -MultipleInstances IgnoreNew
$Principal = New-ScheduledTaskPrincipal `
  -UserId "$env:USERDOMAIN\$env:USERNAME" `
  -LogonType Interactive `
  -RunLevel Limited

Register-ScheduledTask `
  -TaskName $TaskName `
  -Action $Action `
  -Trigger $Trigger `
  -Settings $Settings `
  -Principal $Principal `
  -Force | Out-Null

Write-Output "WINDOWS_STARTUP_TASK=PASS"
Write-Output "TASK_NAME=$TaskName"
