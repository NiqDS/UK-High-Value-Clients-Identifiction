#!/usr/bin/env bash
# =============================================================================
# Install + enable a systemd service so the bot runs 24/7 and restarts on
# crash and on reboot. Run this AFTER setup-server.sh and AFTER creating .env.
#
# Usage (default = the 15m data-collection config):
#     bash install-service.sh
# Or pass a different config:
#     bash install-service.sh config.bybit-live.yaml
#
# TWO BUCKETS side by side — pass a NAME suffix as the 2nd arg to run more than
# one service concurrently (each needs its OWN db_url in its config):
#     bash install-service.sh config.bybit-daily-paper.yaml  daily   # -> tradingbot-daily
#     bash install-service.sh config.bybit-test-active.yaml  15m     # -> tradingbot-15m
# =============================================================================
set -euo pipefail

CONFIG="${1:-config.bybit-test-active.yaml}"
SUFFIX="${2:-}"
APP_DIR="$HOME/UK-High-Value-Clients-Identifiction/tradingbot"
SERVICE_NAME="tradingbot${SUFFIX:+-$SUFFIX}"
RUN_USER="$(id -un)"

if [ ! -f "$APP_DIR/.env" ]; then
  echo "ERROR: $APP_DIR/.env not found. Create it first:"
  echo "  cd $APP_DIR && cp .env.example .env && nano .env"
  exit 1
fi
if [ ! -x "$APP_DIR/.venv/bin/python" ]; then
  echo "ERROR: virtualenv missing. Run deploy/setup-server.sh first."
  exit 1
fi
if [ ! -f "$APP_DIR/$CONFIG" ]; then
  echo "ERROR: config $APP_DIR/$CONFIG not found."
  exit 1
fi

echo "==> writing /etc/systemd/system/${SERVICE_NAME}.service"
echo "    user=$RUN_USER  config=$CONFIG"
sudo tee "/etc/systemd/system/${SERVICE_NAME}.service" >/dev/null <<UNIT
[Unit]
Description=Crypto trading bot (${CONFIG})
After=network-online.target
Wants=network-online.target
# if it crash-loops, back off instead of hammering the exchange
StartLimitIntervalSec=300
StartLimitBurst=10

[Service]
Type=simple
User=${RUN_USER}
WorkingDirectory=${APP_DIR}
# Unbuffered stdout/stderr so logs reach journald in REAL TIME. Without this,
# Python block-buffers under systemd and the service looks silent/dead even
# while running — which made the last incident impossible to diagnose live.
Environment=PYTHONUNBUFFERED=1
ExecStart=${APP_DIR}/.venv/bin/python -m tradingbot --config ${CONFIG} run
Restart=always
RestartSec=10
# Contain memory so a spike is throttled on THIS service instead of pushing the
# whole (small) box into swap-thrash. Soft cap reclaims first; hard cap OOM-kills
# only this unit (systemd then restarts it) rather than freezing the instance.
MemoryHigh=320M
MemoryMax=400M
# graceful shutdown: the runner stops cleanly on SIGTERM
KillSignal=SIGTERM
TimeoutStopSec=30
# light hardening
NoNewPrivileges=true
ProtectSystem=full
PrivateTmp=true

[Install]
WantedBy=multi-user.target
UNIT

sudo systemctl daemon-reload
sudo systemctl enable "${SERVICE_NAME}.service"
sudo systemctl restart "${SERVICE_NAME}.service"
sleep 2
sudo systemctl --no-pager --full status "${SERVICE_NAME}.service" | head -20

cat <<MSG

------------------------------------------------------------------------------
Service installed and running. It now survives crashes AND reboots.

  live logs:      journalctl -u ${SERVICE_NAME} -f
  status:         systemctl status ${SERVICE_NAME}
  restart:        sudo systemctl restart ${SERVICE_NAME}
  stop:           sudo systemctl stop ${SERVICE_NAME}
  DB progress:    cd ${APP_DIR} && ./.venv/bin/python -m tradingbot --config ${CONFIG} db-stats
------------------------------------------------------------------------------
MSG
