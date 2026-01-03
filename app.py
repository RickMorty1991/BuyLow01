from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
)
from telegram.error import Conflict, RetryAfter, TimedOut
from config import BOT_TOKEN, CHECK_INTERVAL
from db import init_db
from handlers import start, add
from callbacks import callbacks
import asyncio
from monitor import check_prices
import sys
import time


async def error_handler(update, context):
    """Handle errors during update processing."""
    error = context.error
    
    if isinstance(error, RetryAfter):
        print(f"⚠️  Rate limited. Waiting {error.retry_after} seconds...")
        await asyncio.sleep(error.retry_after)
        return
    
    if isinstance(error, TimedOut):
        print("⚠️  Request timed out. Retrying...")
        return
    
    # Log other errors (Conflict errors in updater loop are handled automatically)
    print(f"❌ Error: {error}", file=sys.stderr)


async def price_loop(app: Application):
    await asyncio.sleep(10)
    while True:
        try:
            await check_prices(app)
        except Exception as e:
            print("Price loop error:", e)
        await asyncio.sleep(CHECK_INTERVAL)


async def post_init(app: Application):
    # Close any existing webhook to avoid conflicts
    try:
        await app.bot.delete_webhook(drop_pending_updates=True)
        print("✅ Webhook cleared (if any)")
    except Exception as e:
        print(f"⚠️  Could not clear webhook: {e}")
    
    # Additional delay to ensure any previous instance has fully stopped
    # This is especially important during Render deployments
    print("⏳ Waiting for any previous instance to stop...")
    await asyncio.sleep(5)
    
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
    
    # Error handler for Conflict and other errors
    app.add_error_handler(error_handler)

    app.post_init = post_init

    print("✅ BuyLow Bot запущений (Render)")
    
    # Add a delay before starting polling to avoid conflicts during deployment
    # This gives time for any previous instance to fully stop
    print("⏳ Waiting 10 seconds before starting polling...")
    time.sleep(10)
    
    try:
        # The library will automatically retry on Conflict errors
        # bootstrap_retries controls how many times to retry on startup
        app.run_polling(
            drop_pending_updates=True,
            allowed_updates=["message", "callback_query"],
            close_loop=False,
            stop_signals=None  # Let Render handle signals
        )
    except KeyboardInterrupt:
        print("Bot stopped by user")
    except Exception as e:
        print(f"❌ Unexpected error: {e}")
        raise


if __name__ == "__main__":
    main()
