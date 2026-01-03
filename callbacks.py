from telegram import Update
from telegram.ext import ContextTypes
from db import remove_etf, get_all_etfs


async def callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    data = query.data
    print("BUTTON:", data)  # для логів Render

    # remove:AAPL
    if data.startswith("remove:"):
        ticker = data.split(":", 1)[1]
        remove_etf(ticker)

        etfs = get_all_etfs()
        if not etfs:
            await query.edit_message_text("📭 Список порожній")
            return

        text = "📉 Відстежувані ETF:\n"
        for t, price in etfs:
            text += f"• {t} — {price}\n"

        await query.edit_message_text(text)
