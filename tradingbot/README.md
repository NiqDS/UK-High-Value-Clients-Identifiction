# tradingbot — risk-first, human-approved crypto trading bot

A production-minded automated trading program that connects to a single
regulated exchange via [`ccxt`](https://github.com/ccxt/ccxt). Every order is
gated by a **hard risk layer** and an optional **human-approval step over
Telegram**. Capital preservation is prioritised over returns, and every safety
setting defaults to its most conservative value.

> **Guiding principle:** volatility is far more predictable than direction, and
> most predictable around known/detectable events. Where this bot touches news
> or macro events its job is to *manage exposure*, not to forecast direction.

---

## ⚠️ Build status — all 8 steps in place

Steps 1–8 are implemented: risk engine, execution, events/kill-switch, Telegram
bot, heartbeat, backtester + walk-forward learning, the live runner (position
tracking, SQLite persistence, trade-only-key self-check, weekly report via
Telegram **and email**), and deployment artifacts (Dockerfile, docker-compose,
systemd). The runner defaults to **paper + sandbox**; validate end-to-end on the
sandbox before pointing it at real funds.

What works today:

- Typed, validated config (`config.yaml`) + secrets (`.env`) via
  `pydantic-settings`, with conservative defaults and load-time validation.
- A thin, venue-agnostic `ccxt` adapter for **read-only** calls: balances,
  tickers, order book, OHLCV, trading fees.
- The **risk engine**: every `OrderIntent` passes 14 ordered gates — trading
  flag, symbol allowlist, intent sanity, daily-loss stop, max trades/notional
  per day, open positions/orders, min/max notional (absolute + %-equity),
  per-trade risk-to-stop cap, spread guard, **fee gate** (rejects any entry
  whose target move can't clear round-trip fees + margin, conservatively using
  taker fees when fallback is allowed), and the **account floor** (projected
  post-trade free balance — a breach flips a persistent halt flag). Rolling
  daily counters with a configurable UTC reset boundary.
- A reference **SMA-crossover strategy** that emits `OrderIntent`s (BUY entries
  with take-profit + stop; SELL exits) — pluggable via `strategy.name`. It also
  applies a **weighted-average-cost (VWAP) valuation filter**:
  - **buy floor** — enter only when price is at/below `buy_valuation_floor_pct`
    vs VWAP (i.e. undervalued); richer prices are skipped;
  - **force-exit ceiling** — when a held position runs above
    `force_exit_overvaluation_pct` over VWAP, it is **force-exited immediately**
    (emergency market order that crosses the spread, bypassing approval and the
    slippage guard) and an **emergency Telegram alert** is sent.

  Both thresholds are adjustable at runtime from Telegram (`/set_buy_floor`,
  `/set_force_exit`). The valuation is shown in the approval message; the
  intermediate calculation is deliberately not logged.

  Refinement knobs (all in `strategy.*`, off by default so the baseline is
  unchanged): **fee-drag controls** (`trade_cooldown_bars`,
  `min_crossover_strength_pct`) for fewer, higher-conviction trades;
  **active VWAP sizing** (`sizing_mode: vwap_scaled`) that scales position size
  up the more undervalued the entry; and **adaptive exits**
  (`exit_mode: atr`, `trailing_enabled`) using volatility-based ATR
  take-profit/stop and a ratcheting trailing stop.
- The **execution layer**: maker-first passive pricing (orders rest as makers),
  taker only when `allow_taker_fallback` is on, a pre-placement **slippage
  guard**, and fee-aware fills. A **paper broker** (idempotent by client order
  id) simulates fills with no exchange contact; a **live broker** places real
  orders behind the same interface.
- The **pipeline** wiring strategy → posture → risk → (approval) → execution →
  trade count, with a pluggable approver (auto-approve in dry-run; Telegram later).
- The **event-risk module**: an economic calendar (FOMC/CPI/PPI/NFP…) that
  applies a protective posture inside a configurable window — opening *before*
  the release for pre-announcement drift — to pause entries, reduce size,
  widen maker spreads, and report window open/close transitions.
- The **news/volatility kill-switch**: a defensive, strategy-independent monitor
  that halts new entries on an abnormal N-sigma price-velocity or volume spike
  and resumes only after a cooldown of normalised volatility (or manual resume).
  It never auto-trades the direction of a shock.
- The **Telegram approval bot**: trade alerts with inline **Approve / Reject**
  buttons and a **timeout auto-reject**; a `/status` report (balance, floor,
  daily counters, event-risk + kill-switch state); `/settings` with runtime
  `/set_threshold`, `/set_max_notional`, `/set_max_trades`, `/set_daily_loss`;
  `/pause` + `/resume` (kill switch); a **chat-ID allowlist** (empty ⇒ nobody
  authorised); and JSON-persisted runtime overrides so changes — and a manual
  pause — survive a restart.
- The **latency/heartbeat monitor**: tracks round-trip API latency and
  connection health; after N consecutive degraded/failed pings it **auto-suspends
  new entries** (and can cancel resting orders), alerts, and **never crash-loops**;
  it auto-resumes after the connection is healthy for a configurable streak.
  Suspension is kept separate from the manual/floor halt.
- The **honest, zero-look-ahead backtester**: at bar *i* the strategy sees only
  `candles[:i+1]` and fills at bar *i+1*'s open; intra-bar stop/take-profit
  (stop-first, worst case); realistic fees + slippage on every fill;
  **out-of-sample split**; a report that shows **gross vs net-of-fees** so a
  strategy that's profitable gross but not net is exposed. See
  [`docs/backtest_report_sample.md`](docs/backtest_report_sample.md).
- The **live runner**: orchestrates strategy → posture → risk → approval →
  execution per symbol on an interval, with **position tracking** (so the VWAP
  force-exit and take-profit/stop monitors run live), a heartbeat task, the
  kill-switch fed from the candle stream, a **trade-only-key startup self-check**
  (refuses to run live if withdrawal scope is detected), and graceful shutdown.
- **SQLite persistence** (SQLAlchemy): the trade log, daily counters, and the
  trading-enabled flag survive restarts (a floor halt / manual pause is not
  silently forgotten).
- A **weekly performance report**: the week's realised P&L net of fees, win
  rate, and per-symbol net **vs a buy-and-hold market benchmark**, delivered to
  Telegram and written to `reports/`, so the algorithm can be assessed and tuned.
- Structured logging with **secret redaction**.
- `check-config`, `healthcheck`, `paper-run` (never live), `backtest`, `run`,
  and `weekly-report` CLI commands.
- A network-free unit-test suite (**151 tests**) covering config, adapter, every
  risk gate + floor-halt, approval routing, strategy signals, execution
  pricing/slippage/idempotency, the full pipeline, event windows + transitions,
  kill-switch trigger/cooldown/manual-resume, posture integration, the approval
  workflow (approve/reject/timeout, settings, persistence, status), the health
  monitor (suspend/resume/blip-reset + pipeline suspend), the backtester
  (no-look-ahead, next-open fills, fee impact, intra-bar stop, OOS split), the
  SQLite store (trade log + counters/flag persistence across restart), position
  tracking, the weekly report + market benchmark, the trade-only-key self-check,
  and the runner's `run_once` (execute, persist, no double-entry, TP/stop exits).

---

## Quick start (paper / sandbox only)

```bash
cd tradingbot
python3 -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"          # installs ccxt, pydantic, etc.

cp .env.example .env             # then fill in sandbox API keys (optional for public data)

python -m tradingbot check-config   # validate config + show a redacted summary
python -m tradingbot healthcheck    # connect to the sandbox and run read-only calls
```

Run the tests:

```bash
pytest -q
```

> The unit tests need only `pydantic`, `pydantic-settings`, `PyYAML`, and
> `pytest`/`pytest-asyncio` — they inject a fake exchange client and never touch
> the network. `ccxt` is imported lazily, only by the live adapter factory.

---

## Configuration

Two files, kept strictly separate:

| File          | Contents                                  | Committed? |
|---------------|-------------------------------------------|------------|
| `config.yaml` | All non-secret tunables (documented)      | ✅ yes |
| `.env`        | API keys, secrets, Telegram token         | ❌ **never** (git-ignored) |

`config.yaml` is fully documented inline. Highlights of the conservative
defaults shipped:

- `app.dry_run: true` — paper mode, no live orders.
- `exchange.sandbox: true` — testnet endpoint.
- `exchange.symbols_allowlist` — only these pairs may ever trade.
- `risk.floor_quote: 1000` — hard reserve that must always remain.
- `risk.max_notional_per_trade_quote: 50` and `... _pct_equity: 2` — the tighter wins.
- `risk.per_trade_risk_pct: 1` — risk ≤1% of equity per trade.
- `fees.maker_first: true`, `allow_taker_fallback: false`,
  `round_trip_fee_safety_margin_pct: 0.20` — never place a trade that can't
  clear round-trip fees.
- `telegram.approval_threshold_quote: 0.0` — **every** trade requires a manual
  tap until you raise it.
- `regime.enabled: false` — the slow overlay is off by default (it's fragile).

Config is validated on load: e.g. `min_notional > max_notional`, negative
floors, or out-of-range percentages are **rejected** before the bot starts.

---

## Creating a TRADE-ONLY API key (security-critical)

Create the exchange API key with **trade + read permissions ONLY**.
**Withdrawals must be disabled on the key.** The bot never calls withdrawal
endpoints; withdrawals stay a manual, human action through the exchange UI +
2FA. A startup self-check (added in a later milestone) will warn loudly if the
key appears to have withdrawal scope.

Put the credentials in `.env` (see `.env.example`). Some venues (Coinbase, OKX)
also require a passphrase → `EXCHANGE_API_PASSWORD`.

---

## Telegram approval bot

1. Create a bot with [@BotFather](https://t.me/BotFather) (`/newbot`) and copy
   the token into `.env` as `TELEGRAM_BOT_TOKEN`. **Never commit the token** —
   if it leaks, `/revoke` it in BotFather and replace it.
2. Find your numeric chat/user ID (e.g. via @userinfobot) and add it to
   `telegram.allowed_chat_ids` in `config.yaml`. **An empty allowlist authorises
   nobody** (conservative default) — the bot ignores every other chat.
3. Set `telegram.approval_threshold_quote`: trades at/above it require a manual
   tap; below it auto-execute (still through the full risk engine). `0.0` means
   *every* trade needs approval.

Commands once running: `/status`, `/settings` (alias `/menu`), `/set_threshold`,
`/set_max_notional`, `/set_max_trades`, `/set_daily_loss`, `/set_buy_floor`
(undervaluation floor for entries), `/set_force_exit` (overvaluation ceiling
for emergency exits), `/pause`, `/resume`.
Approval messages carry symbol, side, size, est. price, notional, est.
round-trip fees, net-of-fee target, and projected balance vs floor, with inline
**Approve / Reject** buttons. No tap before the timeout ⇒ **auto-reject**.
Runtime changes (and a manual pause) persist to `runtime_overrides.json` so a
restart does not silently resume.

## Backtesting & validation

```bash
# Honest, zero-look-ahead backtest with out-of-sample split, net of fees.
python -m tradingbot backtest --source synthetic --bars 1500 --report docs/report.md
python -m tradingbot backtest --source exchange --bars 1000   # real sandbox OHLCV
```

Flags: `--fee` (%/side), `--slippage` (%/fill), `--oos` (out-of-sample
fraction), `--equity`, `--seed`. The engine never lets the strategy see future
bars and fills at the next bar's open; stop/take-profit are evaluated intra-bar
(stop-first). The report prints **gross vs NET-of-fees** for both the in-sample
and out-of-sample segments.

> Validation rules from the brief: test **out-of-sample**, not just in-sample;
> include realistic fees and slippage; a strategy profitable gross but not net
> of fees is **not** profitable. The bundled SMA strategy is a reference for
> wiring only — the sample report shows it is net-negative after fees, as
> expected. Model volatility regimes before trusting any news/regime overlay.

## Walk-forward learning (refine + validate honestly)

```bash
# 1. pull ~12 months of real OHLCV once (needs exchange network):
python -m tradingbot fetch-data --symbol BTC/USD --timeframe 1h --months 12 --out data/btc.csv

# 2. walk it forward over 4 quarters, learning each quarter, scoring the NEXT unseen one:
python -m tradingbot walkforward --source csv --csv data/btc.csv --windows 4 \
    --metric net_return_over_maxdd --report reports/walkforward.md
```

The harness splits the history into N chronological windows and, for each:
scores the **baseline** params, scores the params **learned on the previous
window** on this *unseen* window (the honest out-of-sample test), then learns on
this window and carries it forward. Two learners run side by side:

- **`param_optimizer`** — searches the interpretable refinement params
  (cooldown, crossover strength, sizing, ATR/TP/SL, VWAP floor) to maximise the
  metric. Transparent, hard to overfit.
- **`qlearning`** — a tabular contextual bandit over market regime
  (valuation × volatility) → sizing action (skip / half / full / double),
  reward = net-of-fees trade outcome.

The metric is **net-of-fees return ÷ max drawdown** (risk-adjusted). Because
every window is scored on data the learner never saw, **overfitting shows up as
a gap between the in-sample and `learned(OOS)` columns** — see
[`docs/walkforward_sample.md`](docs/walkforward_sample.md) (synthetic). On a
random walk the optimizer overfits in-sample and the Q-learner correctly learns
to *not trade* — both honest outcomes. Run it on the real CSV to tune for real.

## Running live (paper + sandbox first)

```bash
python -m tradingbot run            # the orchestrated loop (paper + sandbox by default)
python -m tradingbot weekly-report  # print the last 7 days' performance on demand
```

`run` wires everything: per-symbol strategy → posture (event-risk + kill-switch)
→ risk engine → Telegram approval (if configured) → execution, with the
heartbeat auto-suspend, position tracking (drives the VWAP force-exit and
take-profit/stop monitors), SQLite persistence, and graceful shutdown on
SIGINT/SIGTERM. On startup it runs the **trade-only-key self-check** and refuses
to start live if it detects withdrawal scope on the key. The **weekly report**
is generated every 7 days, delivered to Telegram, and written to
`reports/weekly_latest.md`.

Keep `app.dry_run: true` and `exchange.sandbox: true` until you have watched it
run end-to-end. Telegram approval/alerts require a token in `.env` and a
non-empty `telegram.allowed_chat_ids`.

## Architecture (target)

```
strategy → risk engine (floor, limits, fee gate, event/regime state)
         → (if required) Telegram approval → execution → exchange → trade log
```

Modules (those marked ✅ exist as of Step 1):

- `exchange/` ✅ — thin ccxt adapter + typed models.
- `config.py` ✅ — validated settings + secrets.
- `logging_setup.py` ✅ — structured logging + secret redaction.
- `domain.py` ✅ — shared types (`OrderIntent`, `Side`, `OrderType`).
- `risk/` ✅ — the risk engine + persistent state (floor halt, daily counters).
- `strategy/` ✅ — pluggable `generate_signals() -> list[OrderIntent]` (reference SMA crossover).
- `execution/` ✅ — fee-aware, maker-first placement; slippage guard; paper + live brokers; pipeline.
- `events/` ✅ — economic-calendar event-risk + news/volatility kill-switch + combined posture.
- `approval/` ✅ — Telegram approve/reject workflow, runtime settings, status, controls.
- `app/` ✅ — heartbeat monitor, position tracker, trade-only-key self-check, live runner.
- `backtest/` ✅ — zero-look-ahead engine, out-of-sample split, net-of-fees report.
- `store/` ✅ — SQLite (SQLAlchemy) trade log, daily counters, trading-enabled flag.
- `reporting/` ✅ — weekly performance report vs market benchmark.
- `regime/` — optional slow uncertainty/sentiment overlay.

---

## Build order

1. **✅ Scaffold, config, read-only exchange adapter (sandbox).**
2. **✅ Risk engine + tests (floor, limits, daily counters, fee gate, min-notional/spread).**
3. **✅ Execution layer with paper mode (maker-first, slippage guard); full dry-run pipeline.**
4. **✅ Event-risk module + news/volatility kill-switch + tests.**
5. **✅ Telegram bot: alerts, approve/reject, `/settings` menu, status, pause/resume, auth allowlist.**
6. **✅ Latency/heartbeat auto-suspend; backtesting + out-of-sample validation net of fees.**
7. **✅ Live sandbox runner end-to-end (position tracking, SQLite, weekly report, trade-only-key self-check).**
8. **✅ Deployment artifacts (Dockerfile, docker-compose, systemd) + email reports + README.** ← you are here

---

## Weekly report by email

The weekly report can also be emailed on a fixed schedule. In `config.yaml`:

```yaml
reporting:
  weekly_enabled: true
  weekly_day: 0            # 0=Mon .. 6=Sun
  weekly_hour_utc: 8       # 08:00 UTC
  email_enabled: true
  smtp_host: smtp.gmail.com
  smtp_port: 587
  email_from: "you@gmail.com"
  email_to: ["you@gmail.com"]
```

Put SMTP credentials in `.env` (`SMTP_USERNAME`, `SMTP_PASSWORD`). For Gmail,
create an **App Password** (Google Account → Security → 2-Step Verification →
App passwords) — not your normal password. The report still goes to Telegram too
when that's configured.

## Remote / headless deployment (server / VPS)

Runs headless on a VPS or always-on machine near the exchange. No inbound ports
are needed — you control it over Telegram. Keep the server's clock in UTC and
**disable sleep/suspend** if it's a laptop. Apply the exchange's IP allowlist to
the server's egress IP if you enable that on the key.

**Requirements:** Linux (Ubuntu recommended for systemd/Docker) — macOS works
too; either Docker, *or* Python 3.11+. Persistent disk for `data/` (the SQLite
trade log) and `reports/`. A trade-only API key (withdrawals disabled).

### Option A — Docker (simplest)

```bash
cp .env.example .env          # fill in secrets
# edit config.yaml (keep dry_run + sandbox true until validated)
docker compose up -d --build
docker compose logs -f
```

`data/` and `reports/` are mounted as volumes so the trade history survives
restarts and rebuilds. `restart: unless-stopped` brings it back after a reboot.

### Option B — systemd on a cloud server (no Docker)

Full runbook (Yandex Cloud / VK Cloud / any Ubuntu 24.04 VM) in
[`deploy/README.md`](deploy/README.md). In short, on the server:

```bash
git clone https://github.com/NiqDS/UK-High-Value-Clients-Identifiction.git
cd UK-High-Value-Clients-Identifiction/tradingbot
bash deploy/setup-server.sh                    # python, venv, install
cp .env.example .env && nano .env              # fill Bybit key (local only)
bash deploy/install-service.sh                 # generates + enables the service
journalctl -u tradingbot -f                    # follow logs
```

`install-service.sh` writes the systemd unit for the current user/paths, runs
`python -m tradingbot --config <config> run`, restarts on crash and on reboot,
and stops gracefully on SIGTERM (cancel/flush per config, persist state).

## Safety nets

- `--dry-run`/paper mode on by default for the first run.
- Kill switch: one Telegram command + one config flag halts all trading.
- Audit log of every order intent, risk decision, approval, fill, and
  event/kill-switch state change (Step 2+).
