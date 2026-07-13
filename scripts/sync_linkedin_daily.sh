#!/bin/zsh
set -euo pipefail

PROJECT_DIR="/Users/srikanth/Desktop/linkedin_looker_connector"
LOG_DIR="$PROJECT_DIR/logs"

mkdir -p "$LOG_DIR"
cd "$PROJECT_DIR"

"$PROJECT_DIR/venv/bin/python" manage.py sync_linkedin >> "$LOG_DIR/linkedin_sync.log" 2>&1
