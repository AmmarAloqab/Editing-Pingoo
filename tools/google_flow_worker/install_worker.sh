#!/usr/bin/env bash
set -euo pipefail

BASE_DIR="${PINGOO_FLOW_BASE_DIR:-/opt/pingoo-google-flow}"
VENV_DIR="$BASE_DIR/venv"
REPO_DIR="/opt/MoneyPrinterTurbo"

mkdir -p "$BASE_DIR/profile" "$BASE_DIR/downloads" "$BASE_DIR/logs"
python3 -m venv "$VENV_DIR"
"$VENV_DIR/bin/pip" install --upgrade pip
"$VENV_DIR/bin/pip" install -r "$REPO_DIR/tools/google_flow_worker/requirements.txt"

if ! command -v chromium >/dev/null 2>&1 \
  && ! command -v chromium-browser >/dev/null 2>&1 \
  && ! command -v google-chrome >/dev/null 2>&1; then
  "$VENV_DIR/bin/python" -m playwright install chromium
fi

cat >/etc/systemd/system/pingoo-google-flow.service <<'SERVICE'
[Unit]
Description=Pingoo Google Flow Worker
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=/opt/MoneyPrinterTurbo
Environment=PYTHONPATH=/opt/MoneyPrinterTurbo
Environment=PINGOO_FLOW_BASE_DIR=/opt/pingoo-google-flow
Environment=PINGOO_FLOW_WORKER_HOST=172.20.0.1
Environment=PINGOO_FLOW_WORKER_PORT=8767
Environment=FLOW_GENERATION_TIMEOUT_SECONDS=900
Environment=MAX_AUTO_FLOW_SCENES=2
ExecStart=/opt/pingoo-google-flow/venv/bin/python -m tools.google_flow_worker.worker
Restart=on-failure
RestartSec=5
NoNewPrivileges=true

[Install]
WantedBy=multi-user.target
SERVICE

systemctl daemon-reload
systemctl enable pingoo-google-flow.service

if command -v ufw >/dev/null 2>&1 && ufw status | grep -q "Status: active"; then
  ufw allow in on br-22998e3e4839 proto tcp from 172.20.0.0/16 to 172.20.0.1 port 8767 comment "n8n to Pingoo Google Flow worker" || true
fi
