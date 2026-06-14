#!/usr/bin/env bash
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="${APP_DIR}/.venv"
SERVICE_NAME="${BPQ_PORTAL_SERVICE_NAME:-bpq-webmail}"
INSTALL_SYSTEMD=false
INSTALL_DEPS=false
ASSUME_YES=false

for arg in "$@"; do
  case "$arg" in
    --systemd)
      INSTALL_SYSTEMD=true
      ;;
    --install-deps)
      INSTALL_DEPS=true
      ;;
    -y|--yes)
      ASSUME_YES=true
      ;;
    *)
      echo "Unknown argument: $arg" >&2
      echo "Usage: ./install.sh [--install-deps] [--systemd] [-y|--yes]" >&2
      exit 2
      ;;
  esac
done

cd "$APP_DIR"

apt_install_deps() {
  if ! command -v apt-get >/dev/null 2>&1; then
    echo "Automatic dependency install is only supported on apt-based systems." >&2
    echo "Install Python 3, python3-venv, python3-pip, and build-essential, then rerun ./install.sh." >&2
    exit 1
  fi

  local sudo_cmd=""
  if [ "$(id -u)" -ne 0 ]; then
    if ! command -v sudo >/dev/null 2>&1; then
      echo "sudo is required to install packages as a non-root user." >&2
      exit 1
    fi
    sudo_cmd="sudo"
  fi

  $sudo_cmd apt-get update
  $sudo_cmd apt-get install -y python3 python3-venv python3-pip build-essential
}

check_python_deps() {
  local missing=()

  if ! command -v python3 >/dev/null 2>&1; then
    missing+=("python3")
  else
    if ! python3 -m venv --help >/dev/null 2>&1; then
      missing+=("python3-venv")
    fi
  fi

  if [ "${#missing[@]}" -eq 0 ]; then
    return
  fi

  echo "Missing required Python packages: ${missing[*]}"
  echo "On Ubuntu/Debian, install: python3 python3-venv python3-pip build-essential"

  if [ "$INSTALL_DEPS" = true ]; then
    apt_install_deps
    return
  fi

  if [ "$ASSUME_YES" = true ]; then
    apt_install_deps
    return
  fi

  read -r -p "Install required packages with apt-get now? [y/N] " reply
  case "$reply" in
    y|Y|yes|YES)
      apt_install_deps
      ;;
    *)
      echo "Install dependencies, then rerun ./install.sh." >&2
      exit 1
      ;;
  esac
}

create_venv() {
  if [ -d "$VENV_DIR" ] && [ ! -x "${VENV_DIR}/bin/python" ]; then
    echo "Existing .venv is incomplete; recreating it."
    rm -rf "$VENV_DIR"
  fi

  if [ ! -d "$VENV_DIR" ]; then
    python3 -m venv "$VENV_DIR"
  fi

  if [ ! -x "${VENV_DIR}/bin/python" ]; then
    echo "Virtual environment python was not created correctly." >&2
    echo "Install python3-venv and rerun ./install.sh." >&2
    exit 1
  fi

  if ! "${VENV_DIR}/bin/python" -m pip --version >/dev/null 2>&1; then
    "${VENV_DIR}/bin/python" -m ensurepip --upgrade >/dev/null 2>&1 || true
  fi

  if ! "${VENV_DIR}/bin/python" -m pip --version >/dev/null 2>&1; then
    echo "Virtual environment pip is missing or broken." >&2
    echo "Install python3-venv/python3-pip and rerun ./install.sh." >&2
    exit 1
  fi
}

check_python_deps
create_venv

"${VENV_DIR}/bin/python" -m pip install --upgrade pip
"${VENV_DIR}/bin/python" -m pip install -r requirements.txt

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
