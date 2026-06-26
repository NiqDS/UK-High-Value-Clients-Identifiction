# Telegram Price Monitor Bot

A Telegram bot that scrapes product pages, tracks prices, and sends an alert
whenever a monitored price changes.

## Features

- Paste any product URL (or use `/monitor <url>`) — the bot extracts the item
  name and price and asks you to confirm with inline buttons.
- Per-item polling on a configurable interval via APScheduler.
- Multi-strategy price extraction: JSON-LD (`schema.org/Product`), Open Graph /
  product meta tags, `price`/`cost`/`amount` elements, and a currency-symbol
  text sweep.
- Optional **Playwright fallback** that renders JavaScript-heavy pages in
  headless Chromium when the static scraper finds no price.
- SQLite persistence (`aiosqlite`) — monitors survive restarts and jobs are
  reloaded on startup.
- User-agent rotation, retry-once-then-skip error handling, and a warning after
  5 consecutive failed checks.

## Commands

| Command | Description |
| --- | --- |
| `/start` | Welcome message and usage instructions |
| `/monitor <url>` | Start monitoring a URL (or just paste one) |
| `/list` | Show monitored items with last price and interval |
| `/remove` | Pick an item to remove via inline buttons |
| `/interval <minutes>` | Set the global check interval (min 5, default 60) |
| `/stop` | Stop all monitoring |
| `/status` | Bot uptime and total active monitors |

## Setup

```bash
cd price_monitor_bot
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# edit .env and set TELEGRAM_BOT_TOKEN (get one from @BotFather)

# Optional — for the JavaScript-rendering fallback, download the browser:
playwright install chromium

python bot.py
```

> If you don't need JS-rendered sites, set `USE_PLAYWRIGHT_FALLBACK=false` and
> skip `playwright install`. The bot runs fine with the static scraper alone.

## Configuration (`.env`)

| Variable | Default | Meaning |
| --- | --- | --- |
| `TELEGRAM_BOT_TOKEN` | — | Bot token from @BotFather (required) |
| `DEFAULT_CHECK_INTERVAL_MINUTES` | 60 | Interval for newly added items |
| `MIN_CHECK_INTERVAL_MINUTES` | 5 | Minimum interval allowed via `/interval` |
| `MAX_MONITORED_ITEMS_PER_USER` | 10 | Per-chat item cap |
| `DATABASE_PATH` | `price_monitor.db` | SQLite file path |
| `MAX_CONSECUTIVE_FAILURES` | 5 | Failures before warning the user |
| `REQUEST_TIMEOUT_SECONDS` | 20 | Per-request HTTP timeout |
| `USE_PLAYWRIGHT_FALLBACK` | true | Render JS pages when static scraping finds no price |
| `PLAYWRIGHT_EXECUTABLE_PATH` | — | Explicit Chromium path (blank = Playwright's managed browser) |
| `PLAYWRIGHT_TIMEOUT_SECONDS` | 30 | Max seconds for a rendered page load |

## Project layout

```
price_monitor_bot/
├── bot.py                # Entry point, application wiring, startup/shutdown
├── handlers.py           # Command, message and callback-query handlers
├── scraper.py            # Static price extraction + fallback orchestration
├── playwright_scraper.py # Headless-browser fallback for JS pages
├── scheduler.py          # APScheduler job management + price-check logic
├── database.py    # Async SQLite layer
├── models.py      # Data classes
├── config.py      # Env vars and constants
├── requirements.txt
└── .env.example
```

## Notes

- The static scraper handles most sites instantly. For JavaScript-rendered
  pages (e.g. Ticketmaster), the Playwright fallback re-fetches the page in a
  headless browser. If a price still can't be found, the bot tells the user.
- Designed for a single user or small group; no multi-tenant scaling.
