import os
from pathlib import Path
from dotenv import load_dotenv

# Load .env from the project root (one level up from config/)
load_dotenv(Path(__file__).parent.parent / ".env")


class Settings:
    TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
    NEWS_API_KEY: str = os.getenv("NEWS_API_KEY", "")

    NEWS_CHECK_INTERVAL: int = int(os.getenv("NEWS_CHECK_INTERVAL", "900"))
    NEWS_SEARCH_LANGUAGES: list[str] = [
        lang.strip()
        for lang in os.getenv("NEWS_SEARCH_LANGUAGES", "en,ru,de,es,fr,it").split(",")
        if lang.strip()
    ]
    MAX_NEWS_PER_CHECK: int = int(os.getenv("MAX_NEWS_PER_CHECK", "3"))

    DB_PATH: Path = Path(__file__).parent.parent / os.getenv("DB_PATH", "data/sports_bot.db")
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")

    def validate(self) -> None:
        if not self.TELEGRAM_BOT_TOKEN or self.TELEGRAM_BOT_TOKEN == "your_bot_token_here":
            raise ValueError(
                "TELEGRAM_BOT_TOKEN is not configured. "
                "Edit .env and set your token from @BotFather."
            )


settings = Settings()
