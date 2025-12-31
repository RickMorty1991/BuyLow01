import yfinance as yf
import sqlite3
import time
import threading
import io
import matplotlib.pyplot as plt

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters, CallbackQueryHandler

TOKEN = "8404794616:AAHUJeJp_wvOa8poUXcZufJRXXC72pZZpZZgU0"  # твій токен
    plt.title(f"{ticker.upper()} | TOP 365d: {top:.2f} USD")
    buf = io.BytesIO()
    plt.savefig(buf, format="png")
    plt.close()
    buf.seek(0)
    return buf

# --- Monitoring loop ---
def monitor_loop(bot):
    while True:
        rows = c.execute("SELECT ticker, threshold, rebound, last_alert, rebound_sent, top, top_date, chat_id FROM subs").fetchall()
        for t, th, rb, last, rbs, top, top_date, chat_id in rows:
            now = get_price(t)
            ago = get_price_1y_ago(t)
            if now is None:
                continue

            if top == 0:
                new_top, new_date = get_top_365(t)
                if new_top:
                    c.execute("UPDATE subs SET top=?, top_date=? WHERE ticker=? AND chat_id=?", (new_top, new_date, t, chat_id))
                    conn.commit()
                    top, top_date = new_top, new_date
                else:
                    continue

            dd = (top - now) / top * 100
            change = calc_yearly_change(now, ago)
            change_str = f"{change:.2f}%" if change is not None else "N/A"

            msg = (
                f"📉 *Моніторинг {t.upper()}*\n"
                f"Ціна зараз: `{now:.2f} USD`\n"
                f"Зміна за 365 днів: `{change_str}`\n"
                f"Просадка від TOP 365d: `{dd:.2f}%`\n"
                f"TOP 365d: `{top:.2f} USD` ({top_date})\n"
                f"Поріг alert: `{th}%` | Rebound: `{'ON' if rb else 'OFF'}`"
            )

            if dd >= th and last == 0:
                chart = build_chart(t, top)
                try:
                    if chart:
                        bot.send_photo(chat_id=chat_id, photo=chart, caption="⚠️ Падіння!\n" + msg, parse_mode="Markdown")
                    else:
                        bot.send_message(chat_id=chat_id, text="⚠️ Падіння!\n" + msg, parse_mode="Markdown")
                except Exception as e:
                    print("Send error:", e)
                c.execute("UPDATE subs SET last_alert=1, rebound_sent=0 WHERE ticker=? AND chat_id=?", (t, chat_id))
                conn.commit()

            if dd < th and rb == 1 and last == 1 and rbs == 0:
                try:
                    bot.send_message(chat_id=chat_id, text="📈 Відновлення!\n" + msg, parse_mode="Markdown")
                except Exception as e:
                    print("Send error:", e)
                c.execute("UPDATE subs SET rebound_sent=1 WHERE ticker=? AND chat_id=?", (t, chat_id))
                conn.commit()

            if dd < th and last == 1:
                c.execute("UPDATE subs SET last_alert=0 WHERE ticker=? AND chat_id=?", (t, chat_id))
                conn.commit()

        time.sleep(INTERVAL)

# --- Handlers ---
async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    menu = ReplyKeyboardMarkup([
        ["➕ Add ETF", "📌 My ETFs"],
        ["📉 Set Threshold", "📈 Toggle Rebound"],
        ["🔁 Force Check All", "📊 Status"],
        ["❓ Help", "/commands"]
    ], resize_keyboard=True)
    await update.message.reply_text("Вітаю! Обирайте команду з меню 👇", reply_markup=menu)

async def help_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = (
        "ℹ *ETF Monitor Bot — можливості меню:*\n\n"
        "➕ *Add ETF* — додає ETF до моніторингу та підписки.\n"
        "📌 *My ETFs* — показує список підписок і налаштувань.\n"
        "📉 *Set Threshold* — встановлює поріг просадки для сигналу.\n"
        "📈 *Toggle Rebound* — увімкнути/вимкнути сигнал відновлення ціни.\n"
        "🔁 *Force Check All* — негайно перевірити всі ETF і отримати статус.\n"
        "📊 *Status* — показує поточні ціни, TOP 365d і % зміну за 365 днів.\n"
        "❓ *Help* — показати пояснення.\n"
        "/commands — всі доступні slash-команди"
    )
    await update.message.reply_text(msg, parse_mode="Markdown")

async def commands_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = (
        "📌 *Список команд:*\n\n"
        "/start — меню\n"
        "/add <ticker> — додати ETF\n"
        "/list — список ETF\n"
        "/threshold <ticker> — встановити поріг\n"
        "/rebound <ticker> — ON/OFF rebound\n"
        "/status — перевірити статус\n"
        "/commands — всі команди\n"
        "/help — help меню"
    )
    await update.message.reply_text(msg, parse_mode="Markdown")

async def list_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    rows = c.execute("SELECT ticker, threshold, rebound FROM subs WHERE chat_id=?", (update.message.chat_id,)).fetchall()
    if not rows:
        return await update.message.reply_text("📭 Немає ETF у моніторингу. Додайте через ➕ Add ETF або /add SPY")
    msg = "📌 *Ваші ETF:*\n\n" + "\n".join([f"{t.upper()} → поріг {th}% | Rebound: {'ON' if rb else 'OFF'}" for t, th, rb in rows])
    await update.message.reply_text(msg, parse_mode="Markdown")

async def add_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ticker = ctx.args[0].upper() if ctx.args else None
    chat_id = update.message.chat_id
    if not ticker:
        return await update.message.reply_text("❗ Вкажіть тікер. Приклад: /add SPY")

    top, d = get_top_365(ticker)
    if not top:
        return await update.message.reply_text("❗ Не вдалося отримати дані. Перевірте тікер.")

    c.execute("INSERT OR IGNORE INTO subs(ticker, chat_id, threshold, rebound, top, top_date, last_alert, rebound_sent) VALUES(?,?,?,?,?,?,0,0)", (ticker, chat_id, 5, 1, top, d))
    conn.commit()
    await update.message.reply_text(f"✅ Додано {ticker} у підписку та моніторинг")

# --- Run ---
app = Application.builder().token(TOKEN).build()
bot = app.bot

app.add_handler(CommandHandler("start", start))
app.add_handler(CommandHandler("help", help_cmd))
app.add_handler(CommandHandler("commands", commands_cmd))
app.add_handler(CommandHandler("list", list_cmd))
app.add_handler(CommandHandler("add", add_cmd))
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, reply_router))

threading.Thread(target=monitor_loop, args=(bot,), daemon=True).start()
print("Bot running…")
app.run_polling()
