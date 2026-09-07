# Operations Runbook — Bot, Server & Telegram Setup

> **Purpose.** A single reference for how the live bot, the server, the exchange
> connection and the Telegram link are wired — so that when something misbehaves
> you can compare *observed* behaviour against *expected* behaviour, find the
> fault fast, and record any change you make. Keep this file updated whenever the
> setup changes.

_Last updated: 2026-08-27._

---

## 1. Architecture at a glance

| Piece | What it is | Where |
|---|---|---|
| **Live daily bot** | The real-money 7-coin daily Donchian breakout basket. | systemd service `tradingbot`, config `config.bybit-live.yaml` |
| **4h research bucket** | Paper-only 4h experiment feeding the learning loop. **No real money, ever.** | systemd service `tradingbot-4h`, config `config.bybit-4h-paper.yaml` — currently **disabled** to save RAM |
| **Daily paper bucket** | Pre-launch dress rehearsal (paper). Superseded by the live service. | config `config.bybit-daily-paper.yaml` (not currently installed as a service) |
| **Host** | AWS Lightsail, London (eu-west-2), Ubuntu 24.04, **1 GB RAM** + 2 GB swap, static IP. Migrated from the original 512 MB box on 2026-08-27 (512 MB could not reliably start the bot — ccxt import thrashed swap). | Lightsail console (browser SSH) |
| **Exchange** | Bybit **mainnet spot**, USDT-quoted. | — |

**Capital:** ~206 USDT, split into 7 equal ~27-USDT sleeves. Floor 10 + buffer 5
= 15 USDT reserved and untouchable. The bot only ever touches what's in this
account.

**Strategy behaviour (so silence is not alarming):** daily Donchian breakout —
buys a coin *only* when it closes at a new 50-day high, exits on a 15-day low.
Fires ~1–2 entries **per month** across the whole basket and sits ~70% in cash.
**Days or weeks with zero trades are correct.**

---

## 2. The two connections

### 2.1 Exchange (Bybit)

- **Key type:** TRADE + READ, **withdrawals DISABLED**, spot enabled.
- **IP whitelist:** locked to the Lightsail **static IP**. If the server IP ever
  changes (new instance / resize that drops the static IP), the key must be
  re-whitelisted or every call returns an auth error.
- **Secrets:** live in `.env` in the app dir (`~/UK-High-Value-Clients-Identifiction/tradingbot/.env`).
  `.env` is gitignored, local to the server, **never committed, never pasted into chat.**
- **ccxt client:** async (`ccxt.async_support`), `timeout = request_timeout_ms`
  (20 s), rate limiter on, `options.defaultType = spot`.
- **Funds must be in the Spot/Unified wallet**, not the Funding wallet, or the
  balance reads as unavailable / 0.

### 2.2 Telegram

- **Token:** BotFather token in `.env` as `TELEGRAM_BOT_TOKEN`. If it ever leaks,
  revoke via BotFather `/revoke` and replace in `.env`.
- **Chat id:** `460430602` — set as `telegram.allowed_chat_ids: [460430602]` in
  the config **on the host** (kept `[]` in the repo). An empty allowlist means the
  bot ignores every command (nobody authorised).
- **ONE poller per token.** Telegram allows only one `getUpdates` poller per bot
  token. The **live daily** bucket owns it (`commands_enabled: true`). The **4h**
  bucket is **alerts-only** (`commands_enabled: false`) — it sends messages but
  does not poll, so the two never fight (a 409 Conflict).
- **Alert labels:** every alert/report is tagged `[DAILY]` or `[4h]` in the
  headline so you can tell the buckets apart in one chat.
- **Posture:** `require_approval: false` (auto-approve — the validated systematic
  posture; no per-trade tap), `trade_alerts: true` (a message on every buy/sell).

---

## 3. Expected startup log — the golden reference

When the service starts healthy, `journalctl -u tradingbot` shows **these lines,
in this order, then goes quiet.** Use this ladder to locate a stall.

```
=== tradingbot config summary ===        (stdout; dry_run False, creds present)
 ... summary lines ...
=================================
Exchange bybit: LIVE endpoint (real market data) — order routing depends on app.dry_run
Telegram: alerts-only mode (auto-approve; no per-trade tap).
Telegram bot started (allowlist: [460430602])      <-- or: "Telegram failed to start ... continuing WITHOUT alerts"
SECURITY: use a TRADE+READ API key with WITHDRAWALS DISABLED ...
reconcile: tracker matches the exchange (0 position(s)).
Runner started (dry_run=False, symbols=[...]); next weekly: ...; next monthly: ...
```

After `Runner started` the bot is **silent by design** — there is **no
per-heartbeat log and no balance line in the log.** The balance appears **only**
in the `/status` Telegram reply. Silence ≠ broken.

**Diagnostic ladder (where did it stop?):**

| Last line you see | Where it stalled | Most likely cause |
|---|---|---|
| Only the `=== summary ===` | Importing/constructing ccxt (heavy import) | **Memory starvation / swap thrash** |
| `LIVE endpoint` but no `Telegram bot started` | Telegram init (`get_me`) | Telegram unreachable, or memory |
| `Telegram bot started` but no `Runner started` | `load_markets` / `reconcile` | Bybit slow/unreachable, or memory |
| `Runner started` then silence | **Healthy.** Idle between hourly ticks | — (this is correct) |

**Fastest liveness check of all:** tap `/status` in Telegram. If it returns
`equity: … free: …`, the bot is fully alive regardless of the log.

---

## 4. Known failure modes (symptom → cause → fix)

| Symptom | Root cause | Fix |
|---|---|---|
| `/status` **hangs**, no reply | Balance fetch to Bybit stalled; before the fix it had no timeout. | Fixed in code (10 s bound → degrades to `balance: unavailable`). If it still hangs, the **box is out of RAM** — reboot / resize. |
| `/status` shows `balance: unavailable` (occasionally) | One transient balance-fetch miss. | Ignore a one-off. Worry only if it **persists** across many polls (then: RAM or Bybit outage). |
| Service `active` but journald **totally silent**, `/status` dead | (a) logs were block-buffered [fixed: `PYTHONUNBUFFERED=1`]; and/or (b) startup stalled in ccxt import under swap. | Reboot to clear swap; run **one** service on the 512 MB box; resize to 1 GB for two. |
| Box **freezes / needs reboot** | Swap thrash from too little RAM (was the 512 MB box). | Now on 1 GB — should not recur. If it does: reboot to clear swap, and check nothing extra is resident. |
| Kill-switch **flapping** at startup (`TRIGGERED … resuming` per coin) | Harmless historical replay while priming the window; it re-runs ~30 daily bars in ms. **Now disabled on the live daily bucket anyway.** | None. On the 4h bucket it's expected priming noise; settles once on live bars. |
| `telegram.error.TimedOut` at startup | Telegram unreachable at that moment. **Old code let this stop trading.** | Fixed: Telegram start is now non-fatal + timeout-bounded — the trading loop runs regardless. |
| Telegram `Conflict` / 409 | Two pollers on one token. | Only the live bucket may poll (`commands_enabled: true`); 4h stays alerts-only. |
| Bybit `Invalid Api-Key` / `-2008` | A Bybit key sent to the wrong venue, or IP not whitelisted, or wrong wallet. | Verify key venue = Bybit, IP whitelist = current static IP, funds in Spot/Unified. |
| Balance reads 0 in live | Funds in the Funding wallet, not Spot/Unified. | Move funds to Spot/Unified. |
| Commands ignored, no error | `allowed_chat_ids: []` on the host. | `sed`/edit the config to `[460430602]`, restart. |
| Ran server commands on the Mac (`apt-get: command not found`) | Wrong machine. | Server prompt is `ubuntu@ip-172-…`; Mac is `MacBook-Air-Nick`. Run server steps in the **Lightsail browser SSH**. |

---

## 5. Command cheat-sheet (run on the server)

```bash
cd ~/UK-High-Value-Clients-Identifiction/tradingbot

# Health / state
systemctl status tradingbot --no-pager | head -8
systemctl is-active tradingbot tradingbot-4h        # want: active / inactive
free -h                                              # RAM + swap headroom
journalctl -u tradingbot -f                          # live logs (Ctrl-C to exit)
journalctl -u tradingbot --no-pager -n 40            # recent logs
.venv/bin/python -m tradingbot --config config.bybit-live.yaml check-config
.venv/bin/python -m tradingbot --config config.bybit-live.yaml db-stats

# Control
sudo systemctl restart tradingbot
sudo systemctl stop tradingbot
sudo systemctl disable --now tradingbot-4h           # park the 4h bucket (frees RAM)

# Deploy an update
git pull origin claude/wonderful-ride-dzjenn
bash deploy/install-service.sh config.bybit-live.yaml
```

**Golden rule:** always use `.venv/bin/python`, never the system `python3`
(the packages live in the virtualenv).

---

## 6. Config files & locked invariants

| File | Role | Locked invariant |
|---|---|---|
| `config.bybit-live.yaml` | **Live, real money.** | `dry_run: false` **only here**, on the UK host. Sized for 206 USDT (7×~27). |
| `config.bybit-daily-paper.yaml` | Daily paper rehearsal. | `dry_run: true` — **never flip.** |
| `config.bybit-4h-paper.yaml` | 4h research (paper). | `dry_run: true` — **never flip.** Unvalidated forward. |

On-host edits that are **not** in the repo (re-apply after a fresh clone):
- `allowed_chat_ids: [460430602]` in the live config.
- `dry_run: false` in the live config (repo ships it `true` for safety).

---

## 7. Memory reality

- **512 MB was too small** even for one instance: the `ccxt` import spike on top
  of the OS baseline blew past available RAM, fell into swap, and a clean start
  could take *minutes* (or wedge entirely). Confirmed 2026-08-27.
- **Now on 1 GB** (~909 Mi usable). One instance starts in ~15 s with ~370 MB
  free and swap barely touched. The 4h bucket fits alongside the live daily here.
- Migration path if RAM is ever tight again: Lightsail **snapshot → new instance
  on a bigger plan → move the static IP → restart the bot → delete the old box**
  (see §5-style steps in the change log / chat history). The static IP moving
  with it means the **Bybit IP-whitelist stays valid** — no key changes.
- Swap does **not** clear itself — after a bout of thrash, a **reboot** is the
  clean reset.

---

## 8. Recommended hardening (not yet implemented)

Logged here so we don't lose them:

1. **Per-heartbeat "alive" log line** (e.g. `heartbeat: 7 symbols evaluated, 0
   entries, next in 3600s`). Would make an idle bot visibly distinct from a
   stalled one — the single biggest diagnosis gap we hit.
2. **Overall timeout around `bot.start()`** (`asyncio.wait_for`) on top of the
   per-request timeouts, so even a starved hang is time-bounded.
3. **Bounded startup** for `load_markets()` / `reconcile_positions()` with a
   clear "startup step X" log before each, so a stall names its own step.

---

## 9. Change log

Record every operational change here (date — what — why).

- **2026-08-27** — Sized live config for 206 USDT (7×~27 sleeves). Went live
  (`dry_run: false`). Hardened: Telegram start made non-fatal + timeout-bounded;
  `/status` balance fetch bounded to 10 s; service made unbuffered
  (`PYTHONUNBUFFERED=1`) with `MemoryHigh/Max` caps. Parked the 4h bucket to
  relieve RAM pressure on the 512 MB host.
- **2026-08-27 (later)** — **Migrated host 512 MB → 1 GB** (snapshot → new
  instance → moved static IP → restarted → deleted old box). 512 MB couldn't
  reliably start the bot. New instance private IP `ip-172-26-5-52`; static IP and
  Bybit whitelist unchanged.
- **2026-08-27 (later)** — **Disabled the volatility kill-switch on the live
  daily bucket** (`kill_switch.enabled: false`). It can only subtract from a
  long-only closed-daily-bar breakout edge (would veto big up-day breakouts) and
  adds no intraday protection. Daily-loss breaker, floor, sleeve caps, Donchian
  exit and event pauses remain. 4h paper bucket keeps its kill-switch.
- **2026-08-27 (later)** — Re-enabled the **4h research bucket** on the 1 GB host
  (`tradingbot-4h`), now that there's RAM headroom.
