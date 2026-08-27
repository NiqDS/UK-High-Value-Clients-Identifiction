# Telegram approval setup (5 minutes)

The bot requires your approval (a tap in Telegram) for **every entry** before it
places a real order. Exits never wait for approval — they reduce risk and
execute immediately. Without Telegram configured, the bot **refuses to start
live** (paper mode auto-approves with a warning instead).

## 1. Create the bot (BotFather)

1. In Telegram, open a chat with **@BotFather** (verify the blue check).
2. Send `/newbot` → pick a display name → pick a unique username ending in `bot`
   (e.g. `my_trend_approvals_bot`).
3. BotFather replies with an **HTTP API token** like
   `7123456789:AAH8xk...`. Treat it like a password.

## 2. Put the token in `.env` (never in config.yaml)

```
TELEGRAM_BOT_TOKEN=7123456789:AAH8xk...
```

## 3. Find your numeric chat id

1. Open a chat with **your own new bot** and send it any message (e.g. `hi`)
   — this also authorises it to message you back.
2. In a browser, open:
   `https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates`
3. In the JSON, find `"chat":{"id":123456789,...}` — that number is your chat id.
   (Alternative: message `@userinfobot` and it replies with your id.)

## 4. Allowlist your chat id in the config

```yaml
telegram:
  enabled: true
  approval_threshold_quote: 0.0    # 0 = approve every ENTRY
  approval_timeout_seconds: 300    # no tap in 5 min => entry dropped (safe)
  allowed_chat_ids: [123456789]    # <- your id; ONLY these ids can control the bot
```

Only ids in `allowed_chat_ids` can approve trades or issue commands — anyone
else who finds the bot is ignored.

## 5. Verify

Start the bot (`... run`). In Telegram you can now use:

- `/status` — balance, open positions, risk state, kill-switch/event windows
- `/report` — full performance summary (trades, win rate, net P&L, per-coin)
- `/pause` / `/resume` — halt / resume trading manually
- `/settings` (or `/menu`) — runtime controls

When a breakout entry fires you'll get a message with **Approve / Reject**
buttons and the trade details (symbol, side, notional, stop). No tap within
`approval_timeout_seconds` = the entry is dropped and the bot re-evaluates on
the next pass — missing a notification never leaves you in a worse position
than doing nothing.

## Semantics recap (enforced in code)

| Situation | Behaviour |
|---|---|
| Entry, Telegram configured | Held for your Approve/Reject tap |
| Entry, no tap in time | Dropped (re-evaluated next pass) |
| **Exit / stop** | **Executes immediately — never waits for approval** |
| Paper (`dry_run: true`), no Telegram | Auto-approves, logs a warning |
| Live (`dry_run: false`), no Telegram | **Refuses to start** (no silent deadlock) |
