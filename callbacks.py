from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from db import remove_etf, get_all_etfs


async def callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    
    if not query:
        return
    
    try:
        await query.answer()
    except Exception as e:
        print(f"Error answering callback: {e}")

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
        keyboard = []
        
        for t, price in etfs:
            text += f"• {t} — {price}\n"
            keyboard.append([
                InlineKeyboardButton(
                    f"🗑 {t}",
                    callback_data=f"remove:{t}"
                )
            ])

        await query.edit_message_text(
            text,
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
