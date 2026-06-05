#!/usr/bin/env bash
# =============================================================================
#  Sports News Telegram Bot — macOS Orchestrator
#  Usage:
#    ./orchestrator.sh          — full setup + start bot
#    ./orchestrator.sh setup    — setup only (no run)
#    ./orchestrator.sh start    — start bot (assumes setup done)
#    ./orchestrator.sh service  — install as launchd service (auto-start)
#    ./orchestrator.sh remove   — remove launchd service
#    ./orchestrator.sh status   — show bot process status
#    ./orchestrator.sh logs     — tail live log
# =============================================================================
set -euo pipefail

# ── Colours ───────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
BLUE='\033[0;34m'; BOLD='\033[1m'; NC='\033[0m'

info()  { echo -e "${GREEN}[INFO]${NC}  $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
error() { echo -e "${RED}[ERROR]${NC} $*"; }
step()  { echo -e "\n${BLUE}${BOLD}▶ $*${NC}"; }

# ── Paths ─────────────────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="$SCRIPT_DIR/.venv"
ENV_FILE="$SCRIPT_DIR/.env"
ENV_EXAMPLE="$SCRIPT_DIR/.env.example"
PID_FILE="$SCRIPT_DIR/data/bot.pid"
PLIST_LABEL="com.sportsnewsbot.agent"
PLIST_PATH="$HOME/Library/LaunchAgents/${PLIST_LABEL}.plist"

# ── Helpers ───────────────────────────────────────────────────────────────────
banner() {
    echo -e "${BOLD}"
    echo "  ┌──────────────────────────────────────────┐"
    echo "  │   ⚽  Sports News Telegram Bot            │"
    echo "  │       macOS Orchestrator v1.0             │"
    echo "  └──────────────────────────────────────────┘"
    echo -e "${NC}"
}

require_python() {
    step "Checking Python"
    local candidates=(python3.12 python3.11 python3.10 python3.9 python3)
    for cmd in "${candidates[@]}"; do
        if command -v "$cmd" &>/dev/null; then
            local ver
            ver=$("$cmd" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
            local major minor
            IFS='.' read -r major minor <<< "$ver"
            if (( major >= 3 && minor >= 9 )); then
                PYTHON="$cmd"
                info "Using $cmd ($ver)"
                return
            fi
        fi
    done
    error "Python 3.9+ is required."
    error "Install via Homebrew:  brew install python@3.11"
    exit 1
}

create_venv() {
    step "Virtual environment"
    if [[ ! -d "$VENV" ]]; then
        info "Creating .venv …"
        "$PYTHON" -m venv "$VENV"
    else
        info ".venv already exists"
    fi
    # shellcheck disable=SC1091
    source "$VENV/bin/activate"
    info "Activated"
}

install_deps() {
    step "Installing dependencies"
    pip install --upgrade pip --quiet
    pip install -r "$SCRIPT_DIR/requirements.txt" --quiet
    info "All packages installed"
}

configure_env() {
    step "Environment configuration"
    if [[ ! -f "$ENV_FILE" ]]; then
        cp "$ENV_EXAMPLE" "$ENV_FILE"
        warn ".env created from template — please fill in your values"
        echo ""
        echo "  You need to set at minimum:"
        echo "    TELEGRAM_BOT_TOKEN=<token from @BotFather>"
        echo ""
        read -rp "  Press Enter to open .env in your editor (Ctrl-C to abort)…"
        "${EDITOR:-nano}" "$ENV_FILE"
    fi

    if grep -qE "^TELEGRAM_BOT_TOKEN=(your_bot_token_here)?$" "$ENV_FILE"; then
        error "TELEGRAM_BOT_TOKEN is not set in $ENV_FILE"
        error "Edit the file and paste your token, then re-run this script."
        exit 1
    fi
    info ".env configured"
}

prepare_dirs() {
    step "Preparing directories"
    mkdir -p "$SCRIPT_DIR/data" "$SCRIPT_DIR/logs"
    info "data/ and logs/ ready"
}

init_db() {
    step "Database initialisation"
    cd "$SCRIPT_DIR"
    "$VENV/bin/python" scripts/init_db.py
}

# ── Actions ───────────────────────────────────────────────────────────────────
do_setup() {
    require_python
    create_venv
    install_deps
    configure_env
    prepare_dirs
    init_db
    echo ""
    info "Setup complete."
}

do_start() {
    step "Starting bot"
    # shellcheck disable=SC1091
    source "$VENV/bin/activate"
    cd "$SCRIPT_DIR"
    info "Bot running (PID $$). Press Ctrl-C to stop."
    echo ""
    exec "$VENV/bin/python" main.py
}

do_service_install() {
    step "Installing launchd service"
    # shellcheck disable=SC1091
    source "$VENV/bin/activate"

    cat > "$PLIST_PATH" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>              <string>${PLIST_LABEL}</string>
    <key>ProgramArguments</key>
    <array>
        <string>${VENV}/bin/python</string>
        <string>${SCRIPT_DIR}/main.py</string>
    </array>
    <key>WorkingDirectory</key>   <string>${SCRIPT_DIR}</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key> <string>${VENV}/bin:/usr/local/bin:/usr/bin:/bin</string>
    </dict>
    <key>StandardOutPath</key>    <string>${SCRIPT_DIR}/logs/bot.stdout.log</string>
    <key>StandardErrorPath</key>  <string>${SCRIPT_DIR}/logs/bot.stderr.log</string>
    <key>RunAtLoad</key>          <true/>
    <key>KeepAlive</key>          <true/>
    <key>ThrottleInterval</key>   <integer>10</integer>
</dict>
</plist>
PLIST

    launchctl load -w "$PLIST_PATH"
    info "Service installed and started."
    info "It will also auto-start on login."
    echo ""
    info "Manage with:"
    echo "  launchctl stop  $PLIST_LABEL"
    echo "  launchctl start $PLIST_LABEL"
    echo "  ./orchestrator.sh remove"
}

do_service_remove() {
    step "Removing launchd service"
    if [[ -f "$PLIST_PATH" ]]; then
        launchctl unload -w "$PLIST_PATH" 2>/dev/null || true
        rm -f "$PLIST_PATH"
        info "Service removed."
    else
        warn "No service plist found at $PLIST_PATH"
    fi
}

do_status() {
    echo ""
    if launchctl list "$PLIST_LABEL" &>/dev/null; then
        info "launchd service is LOADED"
        launchctl list "$PLIST_LABEL"
    else
        warn "launchd service is not installed"
    fi
    echo ""
    local pids
    pids=$(pgrep -f "python.*main.py" 2>/dev/null || true)
    if [[ -n "$pids" ]]; then
        info "Bot process(es): $pids"
    else
        warn "No bot process found"
    fi
}

do_logs() {
    local log="$SCRIPT_DIR/logs/bot.log"
    if [[ -f "$log" ]]; then
        tail -f "$log"
    else
        error "Log file not found: $log"
        exit 1
    fi
}

# ── Entry point ───────────────────────────────────────────────────────────────
banner

CMD="${1:-run}"
case "$CMD" in
    setup)           do_setup ;;
    start)           do_start ;;
    service)         do_setup && do_service_install ;;
    remove)          do_service_remove ;;
    status)          do_status ;;
    logs)            do_logs ;;
    run|"")
        do_setup
        do_start
        ;;
    *)
        error "Unknown command: $CMD"
        echo "Usage: $0 [setup|start|service|remove|status|logs]"
        exit 1
        ;;
esac
