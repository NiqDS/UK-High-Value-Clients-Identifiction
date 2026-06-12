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

## ⚠️ Build status — Step 2 of 8

Delivered so far: the **scaffold, config system, read-only exchange adapter**
(Step 1) and the **hard risk engine** (Step 2). Execution, event/kill-switch
modules, Telegram approval, latency monitor, and backtesting are **not built
yet** — see [Build order](#build-order). Do not run this against real funds.

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
- Structured logging with **secret redaction**.
- `check-config` and `healthcheck` CLI commands.
- A network-free unit-test suite (**52 tests**: config, adapter, risk state,
  one per risk gate, floor-halt, and approval routing).

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
- `strategy/` — pluggable `generate_signals() -> list[OrderIntent]` (reference SMA crossover).
- `execution/` — fee-aware, maker-first placement with slippage/spread guards.
- `events/` — economic-calendar event-risk + news/volatility kill-switch.
- `regime/` — optional slow uncertainty/sentiment overlay.
- `approval/` — Telegram approve/reject workflow.
- `store/` — SQLite (SQLAlchemy) models & queries.
- `app/` — wiring, main loop, graceful shutdown, kill switch.

---

## Build order

1. **✅ Scaffold, config, read-only exchange adapter (sandbox).**
2. **✅ Risk engine + tests (floor, limits, daily counters, fee gate, min-notional/spread).** ← you are here
3. Execution layer with paper mode (maker-first, slippage guard); full dry-run pipeline.
4. Event-risk module + news/volatility kill-switch + tests.
5. Telegram bot: alerts, approve/reject, `/settings` menu, status, pause/resume, auth allowlist.
6. Latency/heartbeat auto-suspend; backtesting + out-of-sample validation net of fees.
7. Live sandbox end-to-end with manual approval.
8. Deployment artifacts (Dockerfile, compose, systemd) + full README.

---

## Remote / headless deployment (target — Step 8)

Designed to run on a VPS/container near the exchange (latency matters). All
endpoints/keys live in env/config. Document and apply the exchange's IP
allowlist for the server's egress IP. systemd unit, Dockerfile, and
docker-compose ship in Step 8.

## Safety nets

- `--dry-run`/paper mode on by default for the first run.
- Kill switch: one Telegram command + one config flag halts all trading.
- Audit log of every order intent, risk decision, approval, fill, and
  event/kill-switch state change (Step 2+).
