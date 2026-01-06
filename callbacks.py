from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from db import get_all_etfs, toggle_rebound
from monitor import check_prices


def main_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("↘️ Set Threshold", callback_data="action:threshold")],
        [InlineKeyboardButton("📈 Toggle Rebound", callback_data="action:rebound")],
        [InlineKeyboardButton("🔄 Force Check All", callback_data="action:check")]
    ])


async def callback_handler(update, context):
    query = update.callback_query
    await query.answer()
    data = query.data

    # ---- SET THRESHOLD ----
    if data == "action:threshold":
        etfs = get_all_etfs()

        if not etfs:
            await query.edit_message_text("❗ ETF список порожній")
            return

        keyboard = [
            [InlineKeyboardButton(t, callback_data=f"threshold:{t}")]
            for t, _ in etfs
        ]

        await query.edit_message_text(
            "↘️ Оберіть ETF:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    elif data.startswith("threshold:"):
        ticker = data.split(":")[1]
        context.user_data["set_threshold"] = ticker

        await query.edit_message_text(
            f"✏️ Введіть цільову ціну для {ticker}:"
        )

    # ---- TOGGLE REBOUND ----
    elif data == "action:rebound":
        state = toggle_rebound()
        text = "📈 Rebound УВІМКНЕНО" if state else "📉 Rebound ВИМКНЕНО"
        await query.answer(text, show_alert=True)

    # ---- FORCE CHECK ----
    elif data == "action:check":
        await query.edit_message_text("🔄 Перевіряю всі ETF...")
        await check_prices(context)
        await query.edit_message_text(
            "✅ Готово",
            reply_markup=main_menu()
        )
