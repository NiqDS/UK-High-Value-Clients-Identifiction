"""Entry point: wires up the Telegram application, DB and scheduler.

Run with:  python bot.py
"""
from __future__ import annotations

import logging
import time

from telegram import Update
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    MessageHandler,
    filters,
)

import config
import handlers
from database import Database
from scheduler import MonitorScheduler

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
)
logger = logging.getLogger("price_monitor_bot")


async def _post_init(application: Application) -> None:
    """Connect the DB, build the scheduler, and reload persisted jobs."""
    db = Database()
    await db.connect()

    scheduler = MonitorScheduler(db, application)
    scheduler.start()

    application.bot_data[handlers.DB_KEY] = db
    application.bot_data[handlers.SCHED_KEY] = scheduler
    application.bot_data[handlers.START_TIME_KEY] = time.time()

    reloaded = await scheduler.reload_jobs()
    logger.info("Startup complete — %d active monitor(s) resumed.", reloaded)


async def _post_shutdown(application: Application) -> None:
    """Graceful shutdown that saves state and stops the scheduler."""
    scheduler: MonitorScheduler | None = application.bot_data.get(handlers.SCHED_KEY)
    if scheduler is not None:
        scheduler.shutdown()
    db: Database | None = application.bot_data.get(handlers.DB_KEY)
    if db is not None:
        await db.close()
    logger.info("Shutdown complete — state saved.")


def build_application() -> Application:
    config.validate()
    application = (
        ApplicationBuilder()
        .token(config.TELEGRAM_BOT_TOKEN)
        .post_init(_post_init)
        .post_shutdown(_post_shutdown)
        .build()
    )

    application.add_handler(CommandHandler("start", handlers.cmd_start))
    application.add_handler(CommandHandler("monitor", handlers.cmd_monitor))
    application.add_handler(CommandHandler("list", handlers.cmd_list))
    application.add_handler(CommandHandler("remove", handlers.cmd_remove))
    application.add_handler(CommandHandler("interval", handlers.cmd_interval))
    application.add_handler(CommandHandler("stop", handlers.cmd_stop))
    application.add_handler(CommandHandler("status", handlers.cmd_status))
    application.add_handler(CallbackQueryHandler(handlers.on_callback))
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, handlers.on_text)
    )
    return application


def main() -> None:
    application = build_application()
    logger.info("Starting Price Monitor Bot…")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
