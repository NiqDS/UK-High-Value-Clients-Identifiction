"""How far is each basket coin from its 50-day Donchian high?

A read-only monitoring helper: it answers "which coins are closest to firing a
daily entry?" using the bot's EXACT rule — decide on the last CLOSED daily bar,
build the upper channel from the highest HIGH of the 50 bars BEFORE it (no
look-ahead), and an entry triggers when that close exceeds the 50-day high.

Public OHLCV only (no API keys); it does NOT touch the live bot or its database.
Run on the server (which can reach Bybit):

    .venv/bin/python scripts/channel_distance.py
"""
from __future__ import annotations

import datetime as dt

SYMBOLS = ["BTC/USDT", "ETH/USDT", "BNB/USDT", "ADA/USDT",
           "AVAX/USDT", "DOGE/USDT", "TRX/USDT"]
N_ENTRY = 50   # matches strategy.donchian_entry_period in config.bybit-live.yaml


def main() -> None:
    import ccxt  # sync client is plenty for a one-off read

    ex = ccxt.bybit({"enableRateLimit": True, "options": {"defaultType": "spot"}})
    today = dt.datetime.now(dt.timezone.utc).date()

    rows = []
    for sym in SYMBOLS:
        try:
            ohlcv = ex.fetch_ohlcv(sym, timeframe="1d", limit=70)
        except Exception as e:  # noqa: BLE001 - report and continue
            print(f"{sym:10} fetch failed: {type(e).__name__}: {e}")
            continue
        candles = ohlcv[:]
        # drop the still-forming current-UTC-day bar (parity with signal_on_closed_bar)
        last_date = dt.datetime.fromtimestamp(candles[-1][0] / 1000, dt.timezone.utc).date()
        intraday = candles[-1][4]                       # ~live price (forming bar close)
        if last_date >= today:
            candles = candles[:-1]
        if len(candles) < N_ENTRY + 1:
            print(f"{sym:10} insufficient history ({len(candles)} closed bars)")
            continue
        close = candles[-1][4]                           # last CLOSED daily close
        prior = candles[-N_ENTRY - 1:-1]                 # the 50 bars before it
        high50 = max(c[2] for c in prior)                # highest HIGH of the channel
        below = (high50 - close) / high50 * 100.0
        would = close > high50
        intra_below = (high50 - intraday) / high50 * 100.0
        rows.append((below, sym, close, high50, intraday, intra_below, would))

    print(f"\n50-day Donchian breakout proximity — {today} UTC "
          f"(entry = daily CLOSE above the 50-day high)\n")
    print(f"{'coin':9}{'last close':>13}{'50d high':>13}{'below high':>12}"
          f"{'intraday':>13}{'intra vs high':>15}")
    print("-" * 78)
    for below, sym, close, high50, intraday, intra_below, would in sorted(rows):
        flag = "  <<< NEW HIGH — would BUY on close" if would else ""
        intra_txt = (f"+{-intra_below:.2f}% OVER" if intra_below < 0
                     else f"-{intra_below:.2f}%")
        print(f"{sym:9}{close:>13.4f}{high50:>13.4f}{below:>11.2f}%"
              f"{intraday:>13.4f}{intra_txt:>15}{flag}")
    print("\nClosest to a breakout is at the top. 'intraday' is the current "
          "forming bar; the bot only acts on the CLOSE, so 'intra OVER' is an "
          "early tell, not yet a trade.")


if __name__ == "__main__":
    main()
