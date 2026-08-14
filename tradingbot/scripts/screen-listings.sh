#!/usr/bin/env bash
# =============================================================================
# Batch-screen candidate listings against the proven 7-coin basket.
#
# Fill in CANDIDATES below with the coins you want to check, then run:
#     bash scripts/screen-listings.sh
#
# For each candidate it fetches daily history from Binance (longest record),
# screens it against the current 7 with `screen-candidate`, and prints a
# PROMOTE/REJECT summary. Full per-coin reports land in reports/screen/.
#
# Run this where you have network + can reach the data venue (your Mac is
# ideal). It is OFFLINE analysis — it does NOT touch the live bot or the
# running 7-coin config. A PROMOTE only earns the coin a PAPER trial.
# =============================================================================
set -uo pipefail

# --- EDIT ME: candidate coins to screen (base symbol only, no /USDT) --------
CANDIDATES=(
  SUI
  SEI
  # WIF
  # TIA
  # add one per line...
)

# --- config (safe defaults) -------------------------------------------------
INCUMBENTS=(BTC ETH BNB ADA AVAX DOGE TRX)   # the proven basket — leave as-is
EXCHANGE="${EXCHANGE:-binance}"   # override: EXCHANGE=kucoin bash scripts/... if Binance is blocked
QUOTE="USDT"
MONTHS="${MONTHS:-24}"
FEE="${FEE:-0.6}"
SLIP="${SLIP:-0.05}"
DATA="data/screen"
PY="./.venv/bin/python -m tradingbot"

mkdir -p "$DATA" reports/screen

# lowercase without relying on bash 4 (macOS ships bash 3.2)
lc() { printf '%s' "$1" | tr 'A-Z' 'a-z'; }

# fetch SYMBOL -> prints the csv path on success, non-zero on failure.
# caches: skips the download if the csv already exists (rm it to refresh).
fetch() {
  local sym="$1"
  local out="$DATA/$(lc "$sym")_1d.csv"
  if [ ! -s "$out" ]; then
    if ! $PY fetch-data --exchange "$EXCHANGE" --symbol "${sym}/${QUOTE}" \
         --timeframe 1d --months "$MONTHS" --out "$out" >/dev/null 2>&1; then
      return 1
    fi
  fi
  printf '%s' "$out"
}

echo "==> fetching incumbents (${MONTHS}mo daily from ${EXCHANGE})"
ASSET_ARGS=()
for c in "${INCUMBENTS[@]}"; do
  p=$(fetch "$c") || { echo "  ! could not fetch incumbent $c from $EXCHANGE — aborting"; exit 1; }
  ASSET_ARGS+=(--asset "$c=$p")
done

echo "==> screening ${#CANDIDATES[@]} candidate(s)"
echo
printf '%-8s | %-10s | %s\n' "COIN" "VERDICT" "WHY (first reason)"
printf -- '---------+------------+---------------------------------------------\n'

for cand in "${CANDIDATES[@]}"; do
  [ -z "$cand" ] && continue
  cp=$(fetch "$cand")
  if [ $? -ne 0 ]; then
    printf '%-8s | %-10s | %s\n' "$cand" "NO DATA" "not on $EXCHANGE (or delisted) — can't screen"
    continue
  fi
  report="reports/screen/$(lc "$cand").md"
  if $PY screen-candidate --candidate "$cand=$cp" "${ASSET_ARGS[@]}" \
       --fee "$FEE" --slippage "$SLIP" --report "$report" >/dev/null 2>&1; then
    verdict="PROMOTE"
  else
    verdict="REJECT"
  fi
  # first bullet under "### Why" in the report, for an at-a-glance reason
  why=$(awk '/^### Why/{f=1;next} f&&/^- /{sub(/^- /,"");print;exit}' "$report" 2>/dev/null)
  printf '%-8s | %-10s | %s\n' "$cand" "$verdict" "${why:-see report}"
done

echo
echo "Full per-coin reports: reports/screen/*.md"
echo "Cached history:        $DATA/  (delete a csv to re-fetch fresh)"
