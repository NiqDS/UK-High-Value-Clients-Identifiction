# ⚽ Sports News Telegram Bot

A Telegram bot that monitors sports news for any team you choose. It searches sources in **multiple languages** simultaneously, translates everything into **your preferred language**, and delivers formatted posts every **15 minutes**.

---

## Features

| Feature | Detail |
|---|---|
| Multi-language search | Google News RSS queried in up to 6 language contexts simultaneously |
| Auto-translation | Powered by Google Translate (via `deep-translator`, no API key needed) |
| 15-minute polling | APScheduler job runs in the background |
| Per-user settings | Each user picks their own language and team |
| Duplicate prevention | `sent_news` table ensures no repeated articles |
| Auto-cleanup | News older than 7 days is deleted every 24 hours |
| macOS launchd | Optional: install as a persistent macOS service |

---

## Requirements

| Requirement | Version |
|---|---|
| Python | 3.9 or higher |
| Telegram Bot Token | From [@BotFather](https://t.me/BotFather) |
| Internet access | For RSS feeds and translation |

---

## Quick Start (macOS)

```bash
# 1. Clone / enter the project directory
cd sports_news_bot

# 2. Make the orchestrator executable
chmod +x orchestrator.sh

# 3. Run the full setup + start the bot
./orchestrator.sh
```

The orchestrator will:
1. Check Python 3.9+
2. Create a virtual environment (`.venv/`)
3. Install all Python dependencies
4. Create `.env` from the template (and open it for editing)
5. Create `data/` and `logs/` directories
6. Initialise the SQLite database
7. Start the bot

---

## Manual Setup

```bash
# Create virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Configure environment
cp .env.example .env
nano .env          # set TELEGRAM_BOT_TOKEN

# Create directories & initialise DB
mkdir -p data logs
python scripts/init_db.py

# Start the bot
python main.py
```

---

## Configuration (`.env`)

```dotenv
# Required
TELEGRAM_BOT_TOKEN=123456789:ABCdef...

# Optional — newsapi.org key for extra sources
NEWS_API_KEY=

# How often to check for news (seconds). Default: 900 = 15 min
NEWS_CHECK_INTERVAL=900

# Languages to search news in (comma-separated ISO 639-1 codes)
NEWS_SEARCH_LANGUAGES=en,ru,de,es,fr,it

# Max posts sent per user per check cycle
MAX_NEWS_PER_CHECK=3

# SQLite DB path (relative to project root)
DB_PATH=data/sports_bot.db

# Log level: DEBUG | INFO | WARNING | ERROR
LOG_LEVEL=INFO
```

---

## Bot Commands

| Command | Description |
|---|---|
| `/start` | Welcome message + help |
| `/setlang CODE` | Set your language (e.g. `ru`, `en`, `de`) |
| `/setteam Name` | Set the team to follow (e.g. `Real Madrid`) |
| `/status` | Show your current settings |
| `/latest` | Fetch and deliver news right now |
| `/stop` | Pause automatic notifications |
| `/resume` | Resume notifications |
| `/help` | Show command reference |

### Supported Languages

| Code | Language |
|---|---|
| `en` | English |
| `ru` | Русский |
| `de` | Deutsch |
| `es` | Español |
| `fr` | Français |
| `it` | Italiano |
| `pt` | Português |
| `nl` | Nederlands |
| `tr` | Türkçe |
| `ar` | العربية |
| `ja` | 日本語 |
| `zh` | 中文 |
| `ko` | 한국어 |
| `pl` | Polski |
| `uk` | Українська |

---

## macOS Launchd Service (auto-start on login)

```bash
# Install as a persistent background service
./orchestrator.sh service

# Check status
./orchestrator.sh status

# Tail live log
./orchestrator.sh logs

# Remove the service
./orchestrator.sh remove
```

Service plist is installed at:  
`~/Library/LaunchAgents/com.sportsnewsbot.agent.plist`

---

## Architecture

```
sports_news_bot/
├── main.py                 # Entry point — builds Application, registers handlers, starts jobs
├── orchestrator.sh         # macOS setup & launch script
├── requirements.txt
├── .env.example
│
├── config/
│   └── settings.py         # Reads .env, exposes Settings singleton
│
├── bot/
│   ├── handlers.py         # Telegram command handlers (/start, /setteam, …)
│   ├── scheduler.py        # Scheduled jobs: check_and_send_news, cleanup_old_news
│   ├── news_fetcher.py     # Async RSS fetcher (Google News, multi-language)
│   ├── translator.py       # Async wrapper around deep-translator
│   └── database.py         # All SQLite operations via aiosqlite
│
├── scripts/
│   └── init_db.py          # Standalone DB init (run once at setup)
│
├── data/                   # SQLite database (git-ignored)
└── logs/                   # Rotating log files (git-ignored)
```

### Data flow (every 15 minutes)

```
APScheduler job
  └─► get_active_users()           — load users with a team set
       └─► group by (team, lang)   — avoid duplicate fetches
            └─► fetch_team_news()  — concurrent RSS fetches in N languages
                 └─► translate()   — GoogleTranslator (auto-detect → target lang)
                      └─► save to news_cache
                           └─► get_unsent_news()
                                └─► send_message() → Telegram
                                     └─► mark_news_sent()
```

---

## Database Schema

### `users`
| Column | Type | Description |
|---|---|---|
| `user_id` | INTEGER PK | Telegram user ID |
| `username` | TEXT | Telegram @username |
| `first_name` | TEXT | Telegram first name |
| `language` | TEXT | ISO 639-1 code (default: `en`) |
| `team_name` | TEXT | Team to monitor |
| `active` | INTEGER | 1 = active, 0 = paused |
| `created_at` | TIMESTAMP | Registration time |
| `updated_at` | TIMESTAMP | Last settings change |

### `news_cache`
| Column | Type | Description |
|---|---|---|
| `id` | INTEGER PK | Auto-increment |
| `team_name` | TEXT | Lowercased team name |
| `original_lang` | TEXT | Source language |
| `original_title` | TEXT | Original headline |
| `original_content` | TEXT | Original summary |
| `translated_title` | TEXT | Translated headline |
| `translated_content` | TEXT | Translated summary |
| `target_lang` | TEXT | Translation target language |
| `source_url` | TEXT | Article URL (UNIQUE per lang) |
| `source_name` | TEXT | Publication name |
| `published_at` | TIMESTAMP | Article publication time |
| `fetched_at` | TIMESTAMP | Time cached in DB |

### `sent_news`
| Column | Type | Description |
|---|---|---|
| `id` | INTEGER PK | Auto-increment |
| `user_id` | INTEGER FK | Reference to `users` |
| `news_id` | INTEGER FK | Reference to `news_cache` |
| `sent_at` | TIMESTAMP | When delivered |

**Cleanup:** Records in `news_cache` older than 7 days are deleted daily. `sent_news` rows are removed automatically via `ON DELETE CASCADE`.

---

## Logs

Logs are written to `logs/bot.log` with rotation (5 MB max, 3 backups) and to stdout simultaneously.

```bash
# Live tail
tail -f logs/bot.log

# Or via orchestrator
./orchestrator.sh logs
```

---

## Getting a Bot Token

1. Open Telegram and search for **@BotFather**
2. Send `/newbot` and follow the prompts
3. Copy the token (format: `123456789:ABCdef...`)
4. Paste it in `.env` as `TELEGRAM_BOT_TOKEN`

---

## Troubleshooting

| Problem | Solution |
|---|---|
| `TELEGRAM_BOT_TOKEN is not configured` | Edit `.env`, paste your token |
| No news arriving | Check `logs/bot.log`; verify internet access to `news.google.com` |
| Translation fails | Google Translate may rate-limit; reduce `NEWS_SEARCH_LANGUAGES` |
| Bot stops unexpectedly | Install as launchd service (`./orchestrator.sh service`) for auto-restart |
| Old news re-sent | Check `sent_news` table; run `python scripts/init_db.py` to reset schema |
