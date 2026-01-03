from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from db import add_etf, get_all_etfs


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    etfs = get_all_etfs()

    if not etfs:
        await update.message.reply_text("📭 Список порожній. Додай ETF командою /add")
        return

    text = "📉 Відстежувані ETF:\n"
    keyboard = []

    for ticker, price in etfs:
        text += f"• {ticker} — {price}\n"
        keyboard.append([
            InlineKeyboardButton(
                f"🗑 {ticker}",
                callback_data=f"remove:{ticker}"
            )
        ])

    await update.message.reply_text(
        text,
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("❗ Використання: /add AAPL")
        return

    ticker = context.args[0].upper()
    add_etf(ticker)

    await update.message.reply_text(f"✅ {ticker} додано")
