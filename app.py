from telegram.ext import Application, CommandHandler
from config import BOT_TOKEN, CHECK_INTERVAL
from db import init_db
from handlers import start, add
from monitor import check_prices


async def main():
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN is not set" )

    init_db()

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("add", add))

    app.job_queue.run_repeating(check_prices, interval=CHECK_INTERVAL, first=10)

    print("✅ BuyLow Bot запущений (Render)")
    await app.run_polling()


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
