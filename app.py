from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
)
from telegram.error import Conflict
from config import BOT_TOKEN, CHECK_INTERVAL
from db import init_db
from handlers import start, add
from callbacks import callbacks
import asyncio
from monitor import check_prices
import sys


async def price_loop(app: Application):
    await asyncio.sleep(10)
    while True:
        try:
            await check_prices(app)
        except Exception as e:
            print("Price loop error:", e)
        await asyncio.sleep(CHECK_INTERVAL)


async def post_init(app: Application):
    asyncio.create_task(price_loop(app))


def main():
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN is not set")

    init_db()

    app = Application.builder().token(BOT_TOKEN).build()

    # commands
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("add", add))

    # 🔥 КНОПКИ
    app.add_handler(CallbackQueryHandler(callbacks))

    app.post_init = post_init

    print("✅ BuyLow Bot запущений (Render)")
    try:
        app.run_polling(
            drop_pending_updates=True,
            allowed_updates=["message", "callback_query"]
        )
    except Conflict as e:
        print(f"⚠️  Conflict detected: {e}")
        print("Another bot instance is running. Exiting gracefully...")
        sys.exit(0)
    except KeyboardInterrupt:
        print("Bot stopped by user")
    except Exception as e:
        print(f"❌ Unexpected error: {e}")
        raise


if __name__ == "__main__":
    main()
