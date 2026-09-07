# bad_trades — the learning loop's drop folder

The weekly learning loop (`python -m tradingbot learn`, and automatically on the
weekly report cadence) reads this folder. Two kinds of files live here:

1. **Our own losing trades** — the bot appends them automatically to
   `own_losses_<ISO-week>.jsonl` as a human-readable record of what went wrong.
   *(The loop assesses our own trades from the full database, not this file —
   win-rates need winners too — so this file is for your eyes.)*

2. **External bots' logs — you upload these.** Drop any trade log from another
   bot/strategy here and the loop will assess it alongside ours and tell you if
   it out-performed us in comparable conditions.

## Accepted formats (the loader is tolerant)

JSON, JSONL (one object per line), or CSV. Column/field names are matched
flexibly — any of these work:

| Canonical | Accepted names |
|---|---|
| symbol | symbol, pair, ticker, instrument, market, asset, coin |
| side | side, direction, action, type |
| entry_price | entry_price, entry, open, open_price, avg_entry |
| exit_price | exit_price, exit, close, close_price |
| pnl | pnl, realized_pnl, profit, net, net_pnl, result, return |
| risk_pct | risk_pct, risk, risk_percent, risk% |
| reason | reason, note, tag, comment, exit_reason, label |
| ts | ts, timestamp, time, date, datetime |
| source | source, bot, strategy, origin (labels the external bot) |

Minimum useful row: a `symbol` and a `pnl` (or an `entry`+`exit` to infer it).
A `source` field labels which bot a row came from (for the head-to-head). Rows
the loader can't parse are skipped — one odd file never breaks the run.

## What the loop does with them

Buckets P&L by **risk band**, by **symbol**, and by **exit reason**, then emits
**candidate adjustments** where a bucket has enough trades to trust and loses
money (e.g. "trim size in the high-risk band", "review coin X"). These are
*advisory hypotheses* — backtest any change before applying it. Nothing here
auto-modifies the bot.

## Housekeeping

- `.processed.json` tracks which files have been assessed, so each run picks up
  only NEW drops. Run `learn --all` to re-assess everything.
- This folder is gitignored except this README — logs stay local.
