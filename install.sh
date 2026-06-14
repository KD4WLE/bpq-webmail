#!/usr/bin/env bash
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="${APP_DIR}/.venv"
SERVICE_NAME="${BPQ_PORTAL_SERVICE_NAME:-bpq-webmail}"
INSTALL_SYSTEMD=false

for arg in "$@"; do
  case "$arg" in
    --systemd)
      INSTALL_SYSTEMD=true
      ;;
    *)
      echo "Unknown argument: $arg" >&2
      echo "Usage: ./install.sh [--systemd]" >&2
      exit 2
      ;;
  esac
done

cd "$APP_DIR"

if [ ! -d "$VENV_DIR" ]; then
  python3 -m venv "$VENV_DIR"
fi

"${VENV_DIR}/bin/pip" install --upgrade pip
"${VENV_DIR}/bin/pip" install -r requirements.txt

if [ ! -f ".env" ]; then
  cp .env.example .env
  echo "Created .env from .env.example. Edit it before production use."
fi

"${VENV_DIR}/bin/python" - <<'PY'
from app import init_db

init_db()
print("Database initialized.")
PY

if [ "$INSTALL_SYSTEMD" = true ]; then
  if [ "$(id -u)" -ne 0 ]; then
    echo "--systemd must be run as root." >&2
    exit 1
  fi

  WEB_BIND_HOST="$("${VENV_DIR}/bin/python" - <<'PY'
import config
print(config.WEB_BIND_HOST)
PY
)"
  WEB_BIND_PORT="$("${VENV_DIR}/bin/python" - <<'PY'
import config
print(config.WEB_BIND_PORT)
PY
)"

  cat > "/etc/systemd/system/${SERVICE_NAME}.service" <<EOF
[Unit]
Description=BPQ Webmail Portal
After=network.target

[Service]
Type=simple
WorkingDirectory=${APP_DIR}
EnvironmentFile=${APP_DIR}/.env
ExecStart=${VENV_DIR}/bin/uvicorn app:app --host ${WEB_BIND_HOST} --port ${WEB_BIND_PORT}
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

  systemctl daemon-reload
  systemctl enable "${SERVICE_NAME}"
  systemctl restart "${SERVICE_NAME}"
  systemctl status "${SERVICE_NAME}" --no-pager
fi

echo "Install complete."
echo "Edit .env, then run: ${VENV_DIR}/bin/uvicorn app:app --host \$(grep '^WEB_BIND_HOST=' .env | cut -d= -f2) --port \$(grep '^WEB_BIND_PORT=' .env | cut -d= -f2)"
