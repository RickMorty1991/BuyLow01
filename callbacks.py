from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from db import remove_etf, get_all_etfs, add_etf
from utils import get_main_menu_keyboard


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

    # Handle main menu actions
    if data.startswith("action:"):
        action = data.split(":", 1)[1]
        
        if action == "add":
            await query.edit_message_text(
                "➕ Додати ETF\n\n"
                "Відправте команду:\n"
                "`/add TICKER`\n\n"
                "Наприклад: `/add AAPL`",
                reply_markup=get_main_menu_keyboard()
            )
        
        elif action == "list":
            etfs = get_all_etfs()
            if not etfs:
                await query.edit_message_text(
                    "📭 Список порожній\n\n"
                    "Додайте ETF командою /add або кнопкою ➕ Add ETF",
                    reply_markup=get_main_menu_keyboard()
                )
            else:
                text = "📉 Відстежувані ETF:\n\n"
                keyboard = []
                
                for ticker, price in etfs:
                    price_str = f"{price:.2f}" if price else "—"
                    text += f"• {ticker} — {price_str}\n"
                    keyboard.append([
                        InlineKeyboardButton(
                            f"🗑 {ticker}",
                            callback_data=f"remove:{ticker}"
                        )
                    ])
                
                # Add back button
                keyboard.append([
                    InlineKeyboardButton("◀️ Назад", callback_data="action:list")
                ])
                
                await query.edit_message_text(
                    text,
                    reply_markup=InlineKeyboardMarkup(keyboard)
                )
        
        elif action == "threshold":
            await query.edit_message_text(
                "↘️ Встановити поріг\n\n"
                "Функція в розробці.\n"
                "Поки що використовуйте команду /add TICKER PRICE",
                reply_markup=get_main_menu_keyboard()
            )
        
        elif action == "rebound":
            await query.edit_message_text(
                "📈 Перемикач відскоку\n\n"
                "Функція в розробці.",
                reply_markup=get_main_menu_keyboard()
            )
        
        elif action == "check":
            await query.edit_message_text(
                "🔄 Перевірка всіх ETF...\n\n"
                "Функція в розробці.",
                reply_markup=get_main_menu_keyboard()
            )
        
        elif action == "status":
            etfs = get_all_etfs()
            count = len(etfs) if etfs else 0
            await query.edit_message_text(
                f"📊 Статус бота\n\n"
                f"ETF у списку: {count}\n"
                f"Бот працює ✅",
                reply_markup=get_main_menu_keyboard()
            )
        
        elif action == "help":
            help_text = (
                "❓ Довідка\n\n"
                "📌 **Команди:**\n"
                "`/start` - Головне меню\n"
                "`/add TICKER` - Додати ETF\n\n"
                "📌 **Кнопки:**\n"
                "➕ Add ETF - Додати новий ETF\n"
                "📌 My ETFs - Показати список ETF\n"
                "↘️ Set Threshold - Встановити поріг\n"
                "📈 Toggle Rebound - Перемикач відскоку\n"
                "🔄 Force Check All - Перевірити всі\n"
                "📊 Status - Статус бота\n"
                "❓ Help - Ця довідка"
            )
            await query.edit_message_text(
                help_text,
                reply_markup=get_main_menu_keyboard()
            )
    
    # Handle remove action
    elif data.startswith("remove:"):
        try:
            ticker = data.split(":", 1)[1]
            print(f"🗑 Removing ETF: {ticker}")
            remove_etf(ticker)

            etfs = get_all_etfs()
            if not etfs:
                await query.edit_message_text(
                    "📭 Список порожній",
                    reply_markup=get_main_menu_keyboard()
                )
                print("✅ List is now empty")
                return

            text = "📉 Відстежувані ETF:\n\n"
            keyboard = []
            
            for t, price in etfs:
                price_str = f"{price:.2f}" if price else "—"
                text += f"• {t} — {price_str}\n"
                keyboard.append([
                    InlineKeyboardButton(
                        f"🗑 {t}",
                        callback_data=f"remove:{t}"
                    )
                ])
            
            # Add back button
            keyboard.append([
                InlineKeyboardButton("◀️ Назад", callback_data="action:list")
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
