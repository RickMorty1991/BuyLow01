import asyncio
from telegram.ext import Application, CommandHandler
from config import BOT_TOKEN, CHECK_INTERVAL
from db import init_db
from handlers import start, add
from monitor import check_prices


async def price_loop(app: Application):
    await asyncio.sleep(10)  # startup delay
    while True:
        try:
            await check_prices(app)
        except Exception as e:
            print("Price loop error:", e)
        await asyncio.sleep(CHECK_INTERVAL)


async def main():
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN is not set")

    init_db()

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("add", add))

    # ✅ Render-safe background task
    asyncio.create_task(price_loop(app))

    print("✅ BuyLow Bot запущений (Render)")
    await app.run_polling()


if __name__ == "__main__":
    asyncio.run(main())
