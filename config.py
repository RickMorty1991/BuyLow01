import os

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "TOKEN_HERE")
CHECK_INTERVAL = 900  # 15 min
DB_PATH = "etf_bot.db"
