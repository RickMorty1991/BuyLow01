import os

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL", 900))  # секунд
DB_PATH = os.getenv("DB_PATH", "subscriptions.db")
