#!/usr/bin/env bash
# Native (non-Docker) installer for the Meerkat42 Hub and optional Web Dashboard.
#
# Installs an isolated Python virtual environment, generates secure initial
# credentials in /etc/meerkat42/hub.env (without overwriting existing config),
# runs database migrations, and configures hardened systemd service unit(s).
#
# Usage:
#   sudo bash scripts/install-hub.sh [options]
#
# Options:
#   --mode sqlite|postgres    Storage backend (default: sqlite)
#   --database-url URL        Required when --mode postgres
#   --redis-url URL           Optional Redis backplane URL for multi-replica HA
#   --dir PATH                Install directory (default: /opt/meerkat42)
#   --data-dir PATH           Persistent data directory (default: /var/lib/meerkat42)
#   --config-dir PATH         Config directory (default: /etc/meerkat42)
#   --user NAME               System service user (default: meerkat42)
#   --bind HOST               Bind address (default: 127.0.0.1)
#   --port PORT               Hub API port (default: 8080)
#   --with-frontend           Also build & install the Next.js dashboard service
#   --web-port PORT           Web dashboard port (default: 3000)
#   --dry-run                 Print planned steps without modifying the host
#   --uninstall               Stop/disable systemd services (keeps data & config)
#   -h, --help                Show this help text

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

MODE="sqlite"
DATABASE_URL=""
REDIS_URL=""
INSTALL_DIR="/opt/meerkat42"
DATA_DIR="/var/lib/meerkat42"
CONFIG_DIR="/etc/meerkat42"
SERVICE_USER="meerkat42"
BIND_HOST="127.0.0.1"
HUB_PORT="8080"
WITH_FRONTEND=0
WEB_PORT="3000"
DRY_RUN=0
UNINSTALL=0

usage() {
  sed -n '2,25p' "$0" | sed 's/^# \{0,1\}//'
}

log() {
  printf '[meerkat42-install] %s\n' "$*"
}

die() {
  printf '[meerkat42-install] ERROR: %s\n' "$*" >&2
  exit 1
}

run() {
  if [[ "$DRY_RUN" -eq 1 ]]; then
    printf '[dry-run] %s\n' "$*"
  else
    "$@"
  fi
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --mode) MODE="${2:?missing value for --mode}"; shift 2 ;;
    --database-url) DATABASE_URL="${2:?missing value for --database-url}"; shift 2 ;;
    --redis-url) REDIS_URL="${2:?missing value for --redis-url}"; shift 2 ;;
    --dir) INSTALL_DIR="${2:?missing value for --dir}"; shift 2 ;;
    --data-dir) DATA_DIR="${2:?missing value for --data-dir}"; shift 2 ;;
    --config-dir) CONFIG_DIR="${2:?missing value for --config-dir}"; shift 2 ;;
    --user) SERVICE_USER="${2:?missing value for --user}"; shift 2 ;;
    --bind) BIND_HOST="${2:?missing value for --bind}"; shift 2 ;;
    --port) HUB_PORT="${2:?missing value for --port}"; shift 2 ;;
    --with-frontend) WITH_FRONTEND=1; shift ;;
    --web-port) WEB_PORT="${2:?missing value for --web-port}"; shift 2 ;;
    --dry-run) DRY_RUN=1; shift ;;
    --uninstall) UNINSTALL=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) die "Unknown argument: $1" ;;
  esac
done

if [[ "$MODE" != "sqlite" && "$MODE" != "postgres" ]]; then
  die "--mode must be 'sqlite' or 'postgres'"
fi

if [[ "$MODE" == "postgres" && -z "$DATABASE_URL" ]]; then
  die "--database-url is required when --mode postgres is selected"
fi

if [[ "$DRY_RUN" -eq 0 && "${EUID:-$(id -u)}" -ne 0 ]]; then
  die "Please run as root (e.g. sudo bash scripts/install-hub.sh ...), or pass --dry-run to inspect."
fi

if [[ "$UNINSTALL" -eq 1 ]]; then
  log "Removing Meerkat42 systemd services (preserving ${DATA_DIR} and ${CONFIG_DIR})..."
  for svc in meerkat42-web meerkat42-hub; do
    if command -v systemctl >/dev/null 2>&1; then
      run systemctl disable --now "${svc}.service" || true
    fi
    run rm -f "/etc/systemd/system/${svc}.service"
  done
  if command -v systemctl >/dev/null 2>&1; then
    run systemctl daemon-reload
  fi
  log "Services removed. Delete ${INSTALL_DIR}, ${DATA_DIR}, and ${CONFIG_DIR} manually if you want a full wipe."
  exit 0
fi

# Verify toolchain prerequisites
PYTHON_BIN="${PYTHON_BIN:-$(command -v python3 || true)}"
[[ -n "$PYTHON_BIN" ]] || die "python3 (3.11+) is required."

PY_OK="$("$PYTHON_BIN" -c 'import sys; print(int(sys.version_info >= (3, 11)))')"
[[ "$PY_OK" == "1" ]] || die "Python 3.11 or newer is required (found $("$PYTHON_BIN" --version))."

NPM_BIN=""
if [[ "$WITH_FRONTEND" -eq 1 ]]; then
  NPM_BIN="$(command -v npm || true)"
  [[ -n "$NPM_BIN" ]] || die "npm (Node.js 20+) is required when --with-frontend is enabled."
fi

log "Installing Meerkat42 Hub (mode=${MODE}, dir=${INSTALL_DIR}, bind=${BIND_HOST}:${HUB_PORT})"

# 1. Create dedicated system user
if ! id -u "$SERVICE_USER" >/dev/null 2>&1; then
  log "Creating system user '${SERVICE_USER}'..."
  run useradd --system --home-dir "$DATA_DIR" --shell /usr/sbin/nologin "$SERVICE_USER"
fi

# 2. Prepare directories
run mkdir -p "$INSTALL_DIR" "$DATA_DIR" "$CONFIG_DIR"

# 3. Copy runtime sources into INSTALL_DIR (excluding .git, .venv, data, node_modules)
log "Syncing Hub application files to ${INSTALL_DIR}..."
if [[ "$DRY_RUN" -eq 1 ]]; then
  printf '[dry-run] copy hub/, alembic.ini, scripts/, deploy/ into %s\n' "$INSTALL_DIR"
else
  rm -rf "${INSTALL_DIR}/hub" "${INSTALL_DIR}/scripts" "${INSTALL_DIR}/deploy"
  cp -a "${REPO_ROOT}/hub" "${INSTALL_DIR}/hub"
  cp -a "${REPO_ROOT}/scripts" "${INSTALL_DIR}/scripts"
  cp -a "${REPO_ROOT}/deploy" "${INSTALL_DIR}/deploy"
  cp -a "${REPO_ROOT}/alembic.ini" "${INSTALL_DIR}/alembic.ini"
  if [[ "$WITH_FRONTEND" -eq 1 ]]; then
    rm -rf "${INSTALL_DIR}/frontend"
    mkdir -p "${INSTALL_DIR}/frontend"
    tar -C "${REPO_ROOT}/frontend" --exclude='node_modules' --exclude='.next' -cf - . \
      | tar -C "${INSTALL_DIR}/frontend" -xf -
  fi
fi

# 4. Build isolated Python virtual environment
log "Creating Python virtual environment and installing dependencies..."
run "$PYTHON_BIN" -m venv "${INSTALL_DIR}/.venv"
run "${INSTALL_DIR}/.venv/bin/pip" install --upgrade pip -q
run "${INSTALL_DIR}/.venv/bin/pip" install -r "${INSTALL_DIR}/hub/requirements.txt" -q

# 5. Generate initial config without clobbering existing secrets
ENV_FILE="${CONFIG_DIR}/hub.env"
if [[ ! -f "$ENV_FILE" ]]; then
  log "Generating initial configuration at ${ENV_FILE}..."
  if [[ "$DRY_RUN" -eq 1 ]]; then
    printf '[dry-run] write %s with generated ADMIN_TOKEN and AGENT_BOOTSTRAP_TOKEN\n' "$ENV_FILE"
  else
    ADMIN_TOKEN="$("${INSTALL_DIR}/.venv/bin/python" -c 'import secrets; print(secrets.token_urlsafe(32))')"
    AGENT_TOKEN="$("${INSTALL_DIR}/.venv/bin/python" -c 'import secrets; print(secrets.token_urlsafe(32))')"
    if [[ "$MODE" == "sqlite" ]]; then
      DB_URL="sqlite+aiosqlite:///${DATA_DIR}/hub.db"
    else
      DB_URL="$DATABASE_URL"
    fi
    cat >"$ENV_FILE" <<EOF
# Meerkat42 Native Hub Configuration
DATABASE_URL=${DB_URL}
REDIS_URL=${REDIS_URL}
ADMIN_TOKEN=${ADMIN_TOKEN}
AGENT_BOOTSTRAP_TOKEN=${AGENT_TOKEN}
CORS_ORIGINS=http://localhost:${WEB_PORT},http://127.0.0.1:${WEB_PORT}
AUTO_MIGRATE=true
LOG_LEVEL=INFO
EOF
    chmod 600 "$ENV_FILE"
  fi
else
  log "Keeping existing configuration at ${ENV_FILE}"
fi

# 6. Optional frontend build
if [[ "$WITH_FRONTEND" -eq 1 ]]; then
  log "Installing and building Next.js dashboard..."
  if [[ "$DRY_RUN" -eq 1 ]]; then
    printf '[dry-run] (cd %s/frontend && npm ci && npm run build)\n' "$INSTALL_DIR"
  else
    (cd "${INSTALL_DIR}/frontend" && "$NPM_BIN" ci && "$NPM_BIN" run build)
  fi
fi

# 7. Ownership & migrations
run chown -R "${SERVICE_USER}:${SERVICE_USER}" "$DATA_DIR" "$ENV_FILE"
run chown -R root:root "$INSTALL_DIR"

log "Running Alembic database migrations..."
if [[ "$DRY_RUN" -eq 1 ]]; then
  printf '[dry-run] run alembic upgrade head as %s\n' "$SERVICE_USER"
else
  set -a
  # shellcheck disable=SC1090
  source "$ENV_FILE"
  set +a
  (cd "$INSTALL_DIR" && sudo -u "$SERVICE_USER" -E "${INSTALL_DIR}/.venv/bin/python" -m alembic upgrade head)
fi

# 8. Render & enable systemd units
render_unit() {
  local template="$1"
  local dest="$2"
  if [[ "$DRY_RUN" -eq 1 ]]; then
    printf '[dry-run] render %s -> %s\n' "$template" "$dest"
    return
  fi
  sed \
    -e "s|__USER__|${SERVICE_USER}|g" \
    -e "s|__INSTALL_DIR__|${INSTALL_DIR}|g" \
    -e "s|__DATA_DIR__|${DATA_DIR}|g" \
    -e "s|__CONFIG_DIR__|${CONFIG_DIR}|g" \
    -e "s|__BIND_HOST__|${BIND_HOST}|g" \
    -e "s|__HUB_PORT__|${HUB_PORT}|g" \
    -e "s|__WEB_PORT__|${WEB_PORT}|g" \
    -e "s|__NPM_BIN__|${NPM_BIN}|g" \
    "$template" >"$dest"
  chmod 644 "$dest"
}

if command -v systemctl >/dev/null 2>&1; then
  log "Installing systemd service meerkat42-hub.service..."
  render_unit "${REPO_ROOT}/deploy/systemd/meerkat42-hub.service.template" "/etc/systemd/system/meerkat42-hub.service"
  if [[ "$WITH_FRONTEND" -eq 1 ]]; then
    log "Installing systemd service meerkat42-web.service..."
    render_unit "${REPO_ROOT}/deploy/systemd/meerkat42-web.service.template" "/etc/systemd/system/meerkat42-web.service"
  fi
  run systemctl daemon-reload
  run systemctl enable --now meerkat42-hub.service
  if [[ "$WITH_FRONTEND" -eq 1 ]]; then
    run systemctl enable --now meerkat42-web.service
  fi
else
  log "systemctl not found; start manually with: ${INSTALL_DIR}/.venv/bin/uvicorn hub.main:app --host ${BIND_HOST} --port ${HUB_PORT}"
fi

log "Installation complete!"
log "  Hub API:    http://${BIND_HOST}:${HUB_PORT}/docs"
if [[ "$WITH_FRONTEND" -eq 1 ]]; then
  log "  Dashboard:  http://${BIND_HOST}:${WEB_PORT}"
fi
log "  Config:     ${ENV_FILE}"
log "  Data:       ${DATA_DIR}"
