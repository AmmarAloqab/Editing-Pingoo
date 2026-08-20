#!/usr/bin/env bash
set -euo pipefail

ROOT="/opt/MoneyPrinterTurbo/storage/tasks"
LOG="/opt/MoneyPrinterTurbo/logs/storage-cleanup.log"

mkdir -p "$ROOT"
mkdir -p "$(dirname "$LOG")"

echo "=== $(date -Is) cleanup start ===" >> "$LOG"

find "$ROOT" \
  -mindepth 1 \
  -maxdepth 1 \
  -type d \
  -mmin +1440 \
  -print \
  -exec rm -rf -- {} + \
  >> "$LOG" 2>&1

echo "=== $(date -Is) cleanup done ===" >> "$LOG"


# PINGOO_TELEGRAM_MATERIAL_CLEANUP
# Only Telegram supplemental files use the tg-* prefix.
find /opt/MoneyPrinterTurbo/storage/local_videos \
  -maxdepth 1 \
  -type f \
  -name 'tg-*' \
  -mmin +1440 \
  -delete 2>/dev/null || true

