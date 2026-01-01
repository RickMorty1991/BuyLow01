from telegram import Update
from telegram.ext import ContextTypes
from db import get_conn


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 BuyLow Bot\n\n"

        "Додати алерт:\n"

        "/add TICKER PRICE\n"

        "Приклад: /add AAPL 170"

    )


async def add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        ticker = context.args[0].upper()
        price = float(context.args[1])
    except Exception:
        await update.message.reply_text("❌ Формат: /add TICKER PRICE")
        return

    with get_conn() as conn:
        conn.execute(
            "REPLACE INTO subs (chat_id, ticker, threshold) VALUES (?, ?, ?)",
            (update.effective_chat.id, ticker, price)
        )

    await update.message.reply_text(f"✅ Алерт для {ticker} < {price}")
