#!/usr/bin/env bash
# =============================================================================
# One-time server provisioning for the trading bot.
# Target: a fresh Ubuntu 24.04 LTS VM (ships Python 3.12 by default — the
# version the bot is validated on). Safe to re-run: it updates in place.
#
# Usage (on the server, as your normal login user — NOT root):
#     bash setup-server.sh
# =============================================================================
set -euo pipefail

REPO_URL="https://github.com/NiqDS/UK-High-Value-Clients-Identifiction.git"
BRANCH="claude/wonderful-ride-dzjenn"
CLONE_DIR="$HOME/UK-High-Value-Clients-Identifiction"

echo "==> [1/4] system packages (git, python venv/pip)"
sudo apt-get update -y
sudo apt-get install -y git python3-venv python3-pip

echo "==> [2/4] clone or update the repo at $CLONE_DIR"
if [ -d "$CLONE_DIR/.git" ]; then
  git -C "$CLONE_DIR" fetch origin "$BRANCH"
  git -C "$CLONE_DIR" checkout "$BRANCH"
  git -C "$CLONE_DIR" pull --ff-only origin "$BRANCH"
else
  git clone "$REPO_URL" "$CLONE_DIR"
  git -C "$CLONE_DIR" checkout "$BRANCH"
fi

cd "$CLONE_DIR/tradingbot"

echo "==> [3/4] python virtualenv + install"
[ -d .venv ] || python3 -m venv .venv
./.venv/bin/python -m pip install --upgrade pip
./.venv/bin/pip install -e .

echo "==> [4/4] done. Python: $(./.venv/bin/python --version)"

cat <<'MSG'

------------------------------------------------------------------------------
Provisioning complete. NEXT STEPS (in order):

  1. Create the .env with your Bybit key (TRADE+READ, WITHDRAWALS DISABLED):
       cd ~/UK-High-Value-Clients-Identifiction/tradingbot
       cp .env.example .env
       nano .env          # fill EXCHANGE_API_KEY and EXCHANGE_API_SECRET only

  2. Sanity-check the config (expect: creds present, dry_run True):
       ./.venv/bin/python -m tradingbot --config config.bybit-test-active.yaml check-config

  3. Install + start the always-on service:
       bash deploy/install-service.sh
------------------------------------------------------------------------------
MSG
