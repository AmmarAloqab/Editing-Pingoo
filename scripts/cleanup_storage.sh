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
