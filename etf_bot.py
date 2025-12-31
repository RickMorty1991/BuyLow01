import yfinance as yf
import sqlite3
import time
import threading
import io
import matplotlib.pyplot as plt

from telegram import Bot, Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton, CallbackQuery
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters, CallbackQueryHandler

TELEGRAM_TOKEN = "8404794616:AAGnpxFOwx5rG5BThHkH9cstZ0brmsX81kI"  # <-- заміни на новий токен у BotFather
CHAT_ID = 409544912
CHECK_INTERVAL = 600  # 10 хв

# --- Database ---
conn = sqlite3.connect("etf_top.db", check_same_thread=False)
c = conn.cursor()

c.execute("""CREATE TABLE IF NOT EXISTS subs(
    ticker TEXT,
    chat_id INTEGER,
    threshold REAL DEFAULT 5,
    rebound_enabled INTEGER DEFAULT 1,
    last_alerted INTEGER DEFAULT 0,
    rebound_sent INTEGER DEFAULT 0,
    price_ago REAL DEFAULT 0,
    PRIMARY KEY (ticker, chat_id)
)""")
conn.commit()

bot = Bot(token=TELEGRAM_TOKEN)

# --- Helpers ---
# --- Monitoring Loop ---
def monitor_loop():
    while True:
        c.execute("SELECT ticker, threshold, rebound_enabled, last_alerted, rebound_sent FROM subs WHERE chat_id=?", (CHAT_ID,))
        items = c.fetchall()

        for ticker, threshold, rebound_enabled, last_alerted, rebound_sent in items:
            price_now = get_price_now(ticker)
            price_ago = get_price_1y_ago(ticker)
            ath, ath_date = get_ath_1y(ticker)

            if price_now is None or ath is None:
                continue

            dd = (ath - price_now) / ath * 100
            change = calc_change_percent(price_now, price_ago)
            change_str = f"Зміна за 365 днів: {change:.2f}%" if change is not None else "Зміна за 365 днів: N/A"

            msg = (
                f"{ticker}: {price_now:.2f} USD\n"
                f"{change_str}\n"
                f"Просадка від ATH 1Y: {dd:.2f}%\n"
                f"ATH 1Y: {ath:.2f} ({ath_date})\n"
                f"Поріг alert: {threshold}%\n"
                f"Rebound: {'ON' if rebound_enabled==1 else 'OFF'}"
            )

            if dd >= threshold and last_alerted == 0:
                chart = build_chart_bytes(ticker, ath)
                if chart:
                    bot.send_photo(chat_id=CHAT_ID, photo=chart, caption="⚠️ Падіння!\n" + msg)
                else:
                    bot.send_message(chat_id=CHAT_ID, text="⚠️ Падіння!\n" + msg)
                c.execute("UPDATE subs SET last_alerted=1, rebound_sent=0 WHERE ticker=? AND chat_id=?", (ticker, CHAT_ID))
                conn.commit()

            if dd < threshold and rebound_enabled == 1 and last_alerted == 1 and rebound_sent == 0:
                bot.send_message(chat_id=CHAT_ID, text="📈 Відновлення!\n" + msg)
                c.execute("UPDATE subs SET rebound_sent=1 WHERE ticker=? AND chat_id=?", (ticker, CHAT_ID))
                conn.commit()

            if dd >= threshold and rebound_sent == 1:
                c.execute("UPDATE subs SET rebound_sent=0 WHERE ticker=? AND chat_id=?", (ticker, CHAT_ID))
                conn.commit()

        time.sleep(CHECK_INTERVAL)

# --- Bot handlers ---
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        ["➕ Add ETF", "📌 My ETFs"],
        ["📉 Set Threshold", "📈 Toggle Rebound"],
        ["🔁 Force Check All", "📊 Status"],
        ["🗑 Remove ETF", "❓ Help", "📌 Commands"]
    ]
    menu = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    await update.message.reply_text("Вітаю! Використовуйте меню 👇", reply_markup=menu)

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = (
        "🤖 *ETF Monitor Bot — опис меню:*\n\n"
        "➕ *Add ETF* — додати ETF у моніторинг\n"
        "📌 *My ETFs* — список ваших ETF\n"
        "📉 *Set Threshold* — встановити поріг просадки від ATH 1Y\n"
        "📈 *Toggle Rebound* — увімк/вимк алерти відновлення\n"
        "🔁 *Force Check All* — перевірити всі ETF негайно\n"
        "📊 *Status* — ціна зараз, зміна за 365 днів, просадка від ATH 1Y\n"
        "🗑 *Remove ETF* — вибрати і видалити ETF зі списку\n\n"
        "Команди:\n"
        "/start — меню\n"
        "/list — список ETF\n"
        "/status — статус ETF\n"
        "/remove — видалити ETF\n"
        "/help — допомога\n"
        "/commands — список команд"
    )
    await update.message.reply_text(msg, parse_mode="Markdown")

async def commands_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = (
        "📌 *Список команд:*\n\n"
        "/start — відкрити меню\n"
        "/list — показати ваші ETF\n"
        "/status — перевірити статус ETF\n"
        "/remove — видалити ETF зі списку\n"
        "/help — допомога\n"
        "/commands — список команд"
    )
    await update.message.reply_text(msg, parse_mode="Markdown")

async def list_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    items = c.execute("SELECT ticker, threshold FROM subs WHERE chat_id=?", (CHAT_ID,)).fetchall()
    if not items:
        return await update.message.reply_text("❗ Немає ETF у підписках")
    lines = [f"{t} (поріг {th}%)" for t, th in items]
    await update.message.reply_text("📌 *Ваші ETF:*\n\n" + "\n".join(lines), parse_mode="Markdown")

async def status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    items = c.execute("SELECT ticker, threshold, rebound_enabled FROM subs WHERE chat_id=?", (CHAT_ID,)).fetchall()
    if not items:
        return await update.message.reply_text("❗ Немає даних")

    lines=[]
    charts=[]
    for ticker, th, rb in items:
        price_now = get_price_now(ticker)
        price_ago = get_price_1y_ago(ticker)
        ath, ath_date = get_ath_1y(ticker)
        change = calc_change_percent(price_now, price_ago)

        if price_now and ath:
            dd = (ath - price_now) / ath * 100
            yearly = f"{change:.2f}%" if change is not None else "N/A"
            lines.append(f"{ticker}: {price_now:.2f} USD | Δ1Y {yearly} | DD {dd:.2f}% | Rebound {'ON' if rb else 'OFF'}")

            chart = build_chart_bytes(ticker, ath)
            if chart:
                charts.append(chart)

    for chart in charts:
        bot.send_photo(chat_id=CHAT_ID, photo=chart)

    msg="📊 *Статус ETF:*\n\n" + "\n".join(lines)
    await update.message.reply_text(msg, parse_mode="Markdown")

async def remove_etf_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    items = [r[0] for r in c.execute("SELECT ticker FROM subs WHERE chat_id=?", (CHAT_ID,)).fetchall()]
    if not items:
        return await update.message.reply_text("❗ Немає ETF у списку")

    buttons = [[InlineKeyboardButton(f"🗑 {t}", callback_data=f"remove_etf:{t}")] for t in items]
    await update.message.reply_text("Оберіть ETF для видалення:", reply_markup=InlineKeyboardMarkup(buttons))

async def remove_etf_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    _, ticker = q.data.split(":")
    ticker = ticker.upper()

    c.execute("DELETE FROM subs WHERE ticker=? AND chat_id=?", (ticker, CHAT_ID))
    conn.commit()

    await q.message.reply_text(f"✅ ETF *{ticker}* видалено", parse_mode="Markdown")

async def toggle_rebound_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    items = c.execute("SELECT ticker, rebound_enabled FROM subs WHERE chat_id=?", (CHAT_ID,)).fetchall()
    if not items:
        return await update.message.reply_text("❗ Немає ETF")
    responses=[]
    for t, rb in items:
        new_state = 0 if rb == 1 else 1
        c.execute("UPDATE subs SET rebound_enabled=?, rebound_sent=0 WHERE ticker=? AND chat_id=?", (new_state, t, CHAT_ID))
        responses.append(f"{t}: Rebound → {'ON' if new_state else 'OFF'}")
    conn.commit()
    await update.message.reply_text("🔁 *Оновлено:*\n" + "\n".join(responses), parse_mode="Markdown")

# --- Register ---
app = Application.builder().token(TELEGRAM_TOKEN).build()
app.add_handler(CommandHandler("start", start_cmd))
app.add_handler(CommandHandler("help", help_cmd))
app.add_handler(CommandHandler("list", list_cmd))
app.add_handler(CommandHandler("status", status_cmd))
app.add_handler(CommandHandler("commands", commands_cmd))
app.add_handler(CommandHandler("remove", remove_etf_cmd))
app.add_handler(CommandHandler("rebound", toggle_rebound_cmd))
app.add_handler(MessageHandler(filters.Regex("^📈 Toggle Rebound$"), toggle_rebound_cmd))
app.add_handler(MessageHandler(filters.Regex("^🗑 Remove ETF$"), remove_etf_cmd))
app.add_handler(MessageHandler(filters.Regex("^📉 Set Threshold$"), remove_etf_cmd))
app.add_handler(CallbackQueryHandler(remove_etf_handler, pattern="^remove_etf:"))

threading.Thread(target=monitor_loop, daemon=True).start()
print("Bot running…")
app.run_polling()
