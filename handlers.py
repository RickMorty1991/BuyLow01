from telegram import Update
from telegram.ext import ContextTypes
from db import set_threshold
from callbacks import main_menu


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
