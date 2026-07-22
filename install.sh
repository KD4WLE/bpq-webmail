#!/usr/bin/env bash
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
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

find_existing_venv() {
  if [ -n "${BPQ_PORTAL_VENV:-}" ] && [ -x "${BPQ_PORTAL_VENV}/bin/python" ]; then
    printf '%s\n' "$BPQ_PORTAL_VENV"
    return
  fi

  for candidate in "$APP_DIR/.venv" "$APP_DIR/venv"; do
    if [ -x "$candidate/bin/python" ]; then
      printf '%s\n' "$candidate"
      return
    fi
  done

  local service_file="/etc/systemd/system/${SERVICE_NAME}.service"
  if [ -r "$service_file" ]; then
    local exec_path
    exec_path="$(sed -n 's#^ExecStart=\([^ ]*/bin/\)\(python\|uvicorn\).*#\1#p' "$service_file" | head -n 1)"
    if [ -n "$exec_path" ]; then
      local detected
      detected="$(cd "${exec_path}/../.." 2>/dev/null && pwd || true)"
      if [ -n "$detected" ] && [ -x "$detected/bin/python" ]; then
        printf '%s\n' "$detected"
        return
      fi
    fi
  fi

  printf '%s\n' "$APP_DIR/.venv"
}

VENV_DIR="$(find_existing_venv)"

echo "Using virtual environment: $VENV_DIR"

apt_install_deps() {
  if ! command -v apt-get >/dev/null 2>&1; then
    echo "Automatic dependency install is only supported on apt-based systems." >&2
    exit 1
  fi

  local sudo_cmd=""
  if [ "$(id -u)" -ne 0 ]; then
    command -v sudo >/dev/null 2>&1 || {
      echo "sudo is required to install packages as a non-root user." >&2
      exit 1
    }
    sudo_cmd="sudo"
  fi

  $sudo_cmd apt-get update
  $sudo_cmd apt-get install -y python3 python3-venv python3-pip build-essential
}

check_python_deps() {
  if command -v python3 >/dev/null 2>&1 && python3 -m venv --help >/dev/null 2>&1; then
    return
  fi

  echo "Python 3 and python3-venv are required."
  if [ "$INSTALL_DEPS" = true ] || [ "$ASSUME_YES" = true ]; then
    apt_install_deps
    return
  fi

  read -r -p "Install required packages with apt-get now? [y/N] " reply
  case "$reply" in
    y|Y|yes|YES) apt_install_deps ;;
    *) exit 1 ;;
  esac
}

create_venv() {
  if [ -d "$VENV_DIR" ] && [ ! -x "$VENV_DIR/bin/python" ]; then
    echo "Existing virtual environment is incomplete; recreating it."
    rm -rf "$VENV_DIR"
  fi

  if [ ! -x "$VENV_DIR/bin/python" ]; then
    python3 -m venv "$VENV_DIR"
  fi

  "$VENV_DIR/bin/python" -m ensurepip --upgrade >/dev/null 2>&1 || true
  "$VENV_DIR/bin/python" -m pip --version >/dev/null 2>&1 || {
    echo "Virtual environment pip is missing or broken." >&2
    exit 1
  }
}

install_python_requirements() {
  "$VENV_DIR/bin/python" -m pip install --upgrade pip
  "$VENV_DIR/bin/python" -m pip install -r requirements.txt
}

sync_env_file() {
  if [ ! -f .env ]; then
    cp .env.example .env
    echo "Created .env from .env.example."
    return
  fi

  while IFS='=' read -r key value; do
    case "$key" in
      ''|'#'*) continue ;;
    esac
    if ! grep -q "^${key}=" .env; then
      printf '\n%s=%s\n' "$key" "$value" >> .env
      echo "Added ${key} to .env"
    fi
  done < .env.example
}

initialize_database() {
  "$VENV_DIR/bin/python" - <<'PY'
import analytics
import config
from app import init_db

init_db()
with analytics.connect(config.DB_PATH) as conn:
    analytics.init_db(conn)
print(f"Database initialized: {config.DB_PATH}")
PY
}

check_python_deps
create_venv

if ! install_python_requirements; then
  echo "Dependency installation failed; recreating the virtual environment and retrying once." >&2
  rm -rf "$VENV_DIR"
  create_venv
  install_python_requirements
fi

sync_env_file
initialize_database

"$VENV_DIR/bin/python" -m py_compile app.py run.py analytics.py usage_analytics_integration.py config.py

if [ "$INSTALL_SYSTEMD" = true ]; then
  if [ "$(id -u)" -ne 0 ]; then
    echo "--systemd must be run as root." >&2
    exit 1
  fi

  WEB_BIND_HOST="$("$VENV_DIR/bin/python" -c 'import config; print(config.WEB_BIND_HOST)')"
  WEB_BIND_PORT="$("$VENV_DIR/bin/python" -c 'import config; print(config.WEB_BIND_PORT)')"

  cat > "/etc/systemd/system/${SERVICE_NAME}.service" <<EOF
[Unit]
Description=BPQ Webmail Portal
After=network.target

[Service]
Type=simple
WorkingDirectory=${APP_DIR}
EnvironmentFile=${APP_DIR}/.env
ExecStart=${VENV_DIR}/bin/python -m uvicorn run:app --host ${WEB_BIND_HOST} --port ${WEB_BIND_PORT}
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

  systemctl daemon-reload
  systemctl enable "$SERVICE_NAME"
  systemctl restart "$SERVICE_NAME"
  systemctl status "$SERVICE_NAME" --no-pager
fi

echo "Install complete."
echo "Run manually with:"
echo "  ${VENV_DIR}/bin/python -m uvicorn run:app --host \$(grep '^WEB_BIND_HOST=' .env | cut -d= -f2) --port \$(grep '^WEB_BIND_PORT=' .env | cut -d= -f2)"
