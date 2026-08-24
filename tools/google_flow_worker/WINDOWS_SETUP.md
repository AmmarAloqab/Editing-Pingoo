# Pingoo Google Flow Worker on Windows

Google login on VPS is disabled. Google Flow auth must happen on the user's Windows machine with real Google Chrome and a dedicated Pingoo profile.

## Network design

Use a private mesh network such as Tailscale.

- Windows worker binds to the Windows Tailscale IP and port `8767`.
- VPS n8n calls `http://<windows-tailscale-ip>:8767/flow/generate`.
- Worker uploads generated material back to Editing-Pingoo API on the VPS over the same private network.
- Do not expose the worker directly to the public internet.
- Do not store Google passwords, 2FA codes, cookies, or OAuth tokens in the repo.

## Commands

Install:

```powershell
powershell -ExecutionPolicy Bypass -File .\tools\google_flow_worker\windows_install.ps1
```

One-time auth:

```powershell
powershell -ExecutionPolicy Bypass -File .\tools\google_flow_worker\windows_auth.ps1
```

Worker:

```powershell
$env:PINGOO_FLOW_WORKER_HOST="<WINDOWS_TAILSCALE_IP>"
$env:PINGOO_FLOW_WORKER_PORT="8767"
$env:PINGOO_FLOW_HEADLESS="false"
$env:PINGOO_UPLOAD_URL="http://<VPS_TAILSCALE_IP>:18080/api/v1/video_materials"
powershell -ExecutionPolicy Bypass -File .\tools\google_flow_worker\windows_worker.ps1
```
