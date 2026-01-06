from telegram import Update
from telegram.ext import ContextTypes

from callbacks import main_menu
from db import add_etf, set_threshold


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.bot_data["chat_id"] = update.effective_chat.id

    await update.message.reply_text(
        "📊 ETF Monitor",
        reply_markup=main_menu()
    )


async def add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("❗ Використання: /add TICKER")
        return

    ticker = context.args[0].upper()
    add_etf(ticker)

    await update.message.reply_text(
        f"✅ {ticker} додано",
        reply_markup=main_menu()
    )


async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if "set_threshold" not in context.user_data:
        return

    ticker = context.user_data.pop("set_threshold")

    try:
        price = float(update.message.text)
    except ValueError:
        await update.message.reply_text("❌ Введіть число")
        return

    set_threshold(ticker, price)

    await update.message.reply_text(
        f"✅ Поріг для {ticker} встановлено: {price}",
        reply_markup=main_menu()
    )
