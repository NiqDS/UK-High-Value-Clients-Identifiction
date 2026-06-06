import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from telegram import BotCommand
from telegram.ext import Application, CallbackQueryHandler, CommandHandler

from bot.handlers import (
    button_callback,
    help_command,
    latest_command,
    resume_command,
    setlang_command,
    setteam_command,
    start_command,
    status_command,
    stop_command,
)
from bot.scheduler import check_and_send_news, cleanup_old_news
from bot.database import init_database
from config.settings import settings


def _setup_logging() -> None:
    logs_dir = Path(__file__).parent / "logs"
    logs_dir.mkdir(exist_ok=True)

    fmt = logging.Formatter("%(asctime)s | %(name)-25s | %(levelname)-8s | %(message)s")

    file_h = RotatingFileHandler(
        logs_dir / "bot.log",
        maxBytes=5 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    file_h.setFormatter(fmt)

    console_h = logging.StreamHandler(sys.stdout)
    console_h.setFormatter(fmt)

    root = logging.getLogger()
    root.setLevel(getattr(logging, settings.LOG_LEVEL, logging.INFO))
    root.addHandler(file_h)
    root.addHandler(console_h)

    for noisy in ("httpx", "aiohttp", "feedparser", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


_BOT_COMMANDS = [
    BotCommand("start",   "Start the bot / show quick menu"),
    BotCommand("setlang", "Set your language (shows menu)"),
    BotCommand("setteam", "Set team to follow, e.g. /setteam Arsenal"),
    BotCommand("latest",  "Get latest news right now"),
    BotCommand("status",  "Show your current settings"),
    BotCommand("stop",    "Pause automatic notifications"),
    BotCommand("resume",  "Resume automatic notifications"),
    BotCommand("help",    "Show all commands and usage"),
]


async def _post_init(application: Application) -> None:
    await init_database()

    # Register the "/" command menu in Telegram so users can tap instead of type
    await application.bot.set_my_commands(_BOT_COMMANDS)

    jq = application.job_queue
    # Check news every 15 minutes (first run 30 s after startup)
    jq.run_repeating(
        check_and_send_news,
        interval=settings.NEWS_CHECK_INTERVAL,
        first=30,
        name="news_checker",
    )
    # Clean up stale records once a day (first run 1 h after startup)
    jq.run_repeating(
        cleanup_old_news,
        interval=86_400,
        first=3_600,
        name="daily_cleanup",
    )
    logging.getLogger(__name__).info(
        "Bot ready. News interval: %ds", settings.NEWS_CHECK_INTERVAL
    )


def main() -> None:
    _setup_logging()
    logger = logging.getLogger(__name__)

    try:
        settings.validate()
    except ValueError as exc:
        logger.error("Configuration error: %s", exc)
        sys.exit(1)

    logger.info("Starting Sports News Bot…")

    app = (
        Application.builder()
        .token(settings.TELEGRAM_BOT_TOKEN)
        .post_init(_post_init)
        .build()
    )

    app.add_handler(CommandHandler("start",              start_command))
    app.add_handler(CommandHandler("help",               help_command))
    app.add_handler(CommandHandler(["setlang", "lang"],  setlang_command))
    app.add_handler(CommandHandler(["setteam", "team"],  setteam_command))
    app.add_handler(CommandHandler("status",             status_command))
    app.add_handler(CommandHandler("stop",               stop_command))
    app.add_handler(CommandHandler(["resume", "on"],     resume_command))
    app.add_handler(CommandHandler(["latest", "news"],   latest_command))
    app.add_handler(CallbackQueryHandler(button_callback))

    app.run_polling(
        allowed_updates=["message", "callback_query"],
        drop_pending_updates=True,
    )


if __name__ == "__main__":
    main()
