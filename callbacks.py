from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from db import remove_etf, get_all_etfs


async def callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle callback queries from inline keyboard buttons."""
    if not update or not update.callback_query:
        print("⚠️  No callback_query in update")
        return
    
    query = update.callback_query
    data = query.data
    
    if not data:
        print("⚠️  No data in callback_query")
        try:
            await query.answer("❌ Помилка: немає даних")
        except Exception:
            pass
        return
    
    print(f"🔘 BUTTON CLICKED: {data}")  # для логів Render

    try:
        # Answer the callback query first to remove loading state
        await query.answer()
    except Exception as e:
        print(f"⚠️  Error answering callback: {e}")
        # Continue anyway

    # remove:AAPL
    if data.startswith("remove:"):
        try:
            ticker = data.split(":", 1)[1]
            print(f"🗑 Removing ETF: {ticker}")
            remove_etf(ticker)

            etfs = get_all_etfs()
            if not etfs:
                await query.edit_message_text("📭 Список порожній")
                print("✅ List is now empty")
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
            print(f"✅ Successfully removed {ticker} and updated message")
        except Exception as e:
            print(f"❌ Error processing remove callback: {e}")
            import traceback
            traceback.print_exc()
            try:
                await query.answer(f"❌ Помилка: {str(e)}", show_alert=True)
            except Exception:
                pass
    else:
        print(f"⚠️  Unknown callback data: {data}")
        try:
            await query.answer("❌ Невідома команда")
        except Exception:
            pass
