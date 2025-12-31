import yfinance as yf
import sqlite3
import time
import threading
import io
import matplotlib.pyplot as plt

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters, CallbackQueryHandler

# ==== CONFIG ====
TOKEN = "8404794616:AAHUJeJp_wvOa8poUXcZufJRXXC72pZZgU0"
CHAT_ID = 409544912
CHECK_INTERVAL = 600  # 10 хв

# ==== DATABASE ====
db = sqlite3.connect("etf_top.db", check_same_thread=False)
c = db.cursor()
c.execute("""
CREATE TABLE IF NOT EXISTS subs(
    ticker TEXT,
    chat_id INTEGER,
    threshold REAL DEFAULT 5.0,
    rebound_enabled INTEGER DEFAULT 1,
    last_alerted INTEGER DEFAULT 0,
    rebound_sent INTEGER DEFAULT 0,
    price_ago REAL DEFAULT 0,
    PRIMARY KEY (ticker, chat_id)
)
""")
db.commit()

# ==== FINANCE HELPERS ====
def get_price_now(t):
    df = yf.Ticker(t).history(period="1d")
    return float(df["Close"].iloc[-1]) if not df.empty else None

def get_price_1y_ago(t):
    df = yf.Ticker(t).history(period="1y")
    return float(df["Close"].iloc[0]) if not df.empty else None

def get_ath_1y(t):
    df = yf.Ticker(t).history(period="1y")
    if df.empty:
        return None, None
    ath = float(df["Close"].max())
    ath_date = df["Close"].idxmax().strftime("%Y-%m-%d")
    return ath, ath_date

def calc_change_percent(now, ago):
    if ago is None or ago == 0:
        return None
    return (now - ago) / ago * 100

def build_chart(t, ath):
    df = yf.Ticker(t).history(period="1y")
    hist = df["Close"]
    if hist.empty or ath is None:
        return None
    plt.figure()
    plt.plot(hist)
    plt.axhline(ath)
    plt.title(f"{t} | ATH 1Y {ath:.2f} USD")
    buf = io.BytesIO()
    plt.savefig(buf, format="png")
    plt.close()
    buf.seek(0)
    return buf

# ==== BOT FUNCTIONS ====
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        ["➕ Add ETF", "📊 Status"],
        ["📉 Set Threshold", "📈 Toggle Rebound"],
        ["📌 My ETFs", "🔁 Force Check All"],
        ["❓ Help", "📌 Commands"]
    ]
    await update.message.reply_text("Вітаю! Оберіть команду 👇", reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True))

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "📘 *Help Menu*\n\n"
        "➕ Add ETF — додати ETF у моніторинг\n"
        "📌 My ETFs — показати список ETF + видалення\n"
        "📉 Set Threshold — встановити поріг просадки від річного ATH\n"
        "📈 Toggle Rebound — увімк/вимк сигнал відновлення ціни після падіння\n"
        "📊 Status — показати поточну ціну + графіки\n"
        "🔁 Force Check All — негайно перевірити всі ETF\n"
        "🗑 Remove — видалити ETF через список\n"
        "📌 Commands — список усіх команд"
    )
    await update.message.reply_text(text, parse_mode="Markdown")

async def commands_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "📌 *Список доступних команд:*\n\n"
        "/start — відкрити меню\n"
        "/list — список ETF\n"
        "/status — статус цін + графіки\n"
        "/threshold <ticker> <percent> — поріг DD\n"
        "/rebound <ticker> ON/OFF — toggle rebound\n"
        "/remove <ticker> — прибрати ETF\n"
        "/help — довідка меню\n"
        "/commands — список команд"
    )
    await update.message.reply_text(text, parse_mode="Markdown")

async def list_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    rows = c.execute("SELECT ticker, threshold, rebound_enabled FROM subs WHERE chat_id=?", (CHAT_ID,)).fetchall()
    if not rows:
        return await update.message.reply_text("❗ Немає ETF у списку")

    buttons = []
    for ticker, th, rb in rows:
        buttons.append([InlineKeyboardButton(f"{ticker} | 🗑 Remove", callback_data=f"remove:{ticker}")])

    await update.message.reply_text("📌 *Ваші ETF:* 👇", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(buttons))

async def remove_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    parts = context.args
    if len(parts) < 1:
        return await update.message.reply_text("Вкажіть ticker для видалення")

    ticker = parts[0].upper()
    c.execute("DELETE FROM subs WHERE ticker=? AND chat_id=?", (ticker, CHAT_ID))
    db.commit()
    await update.message.reply_text(f"🗑 {ticker} видалено зі списку")
    await list_cmd(update, context)

async def threshold_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    parts = context.args
    if len(parts) < 2:
        return await update.message.reply_text("Формат: /threshold SPY 4")

    ticker = parts[0].upper()
    val = float(parts[1])

    row = c.execute("SELECT 1 FROM subs WHERE ticker=? AND chat_id=?", (ticker, CHAT_ID)).fetchone()
    if not row:
        return await update.message.reply_text("❗ Немає такого ETF")

    c.execute("UPDATE subs SET threshold=? WHERE ticker=? AND chat_id=?", (val, ticker, CHAT_ID))
    db.commit()
    await update.message.reply_text(f"✔ Поріг для {ticker} = {val}%")

async def rebound_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    parts = context.args
    if len(parts) < 2:
        return await update.message.reply_text("Формат: /rebound QQQ ON")

    ticker = parts[0].upper()
    state = parts[1].upper()

    row = c.execute("SELECT 1, rebound_enabled FROM subs WHERE ticker=? AND chat_id=?", (ticker, CHAT_ID)).fetchone()
    if not row:
        return await update.message.reply_text("❗ Немає такого ETF")

    new_val = 1 if state == "ON" else 0
    c.execute("UPDATE subs SET rebound_enabled=?, rebound_sent=0 WHERE ticker=? AND chat_id=?", (new_val, ticker, CHAT_ID))
    db.commit()
    await update.message.reply_text(f"🔁 Rebound {ticker}: {state}")

async def status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    rows = c.execute("SELECT ticker FROM subs WHERE chat_id=?", (CHAT_ID,)).fetchall()
    if not rows:
        return await update.message.reply_text("❗ Немає ETF")

    for (ticker,) in rows:
        now = get_price_now(ticker)
        ath, ath_date = get_ath_1y(ticker)
        ago = get_price_1y_ago(ticker)
        change = calc_change_percent(now, ago) if now and ago else None
        dd = (ath - now) / ath * 100 if ath and now else None

        text = f"📊 {ticker}\n💰 Ціна: {now:.2f} USD\n📆 ATH1Y: {ath:.2f} ({ath_date})\n"
        if change is not None:
            arrow = "📈" if change > 0 else "📉"
            text += f"{arrow} Δ365d: {change:.2f}%\n"
        else:
            text += "Δ365d: N/A\n"
        if dd is not None:
            text += f"📉 DD від ATH1Y: {dd:.2f}%\n"

        buf = build_chart(ticker, ath)
        if buf:
            await update.message.reply_photo(buf, caption=text)
        else:
            await update.message.reply_text(text)

    await update.message.reply_text("📊 Status оновлено ✔")

# ==== INLINE CALLBACK HANDLER ====
async def remove_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    ticker = query.data.split(":")[1].upper()

    c.execute("DELETE FROM subs WHERE ticker=? AND chat_id=?", (ticker, CHAT_ID))
    db.commit()

    await query.message.reply_text(f"🗑 {ticker} видалено")
    await list_cmd(update, context)

# ==== MONITOR THREAD (one instance) ====
def monitor_loop_thread():
    while True:
        for ticker, threshold, rebound_enabled, last_alerted, rebound_sent, pa1 in c.execute("SELECT ticker,threshold,rebound_enabled,last_alerted,rebound_sent,price_ago FROM subs WHERE chat_id=?", (CHAT_ID,)):
            now = get_price_now(ticker)
            ath, ath_date = get_ath_1y(ticker)
            if now is None or ath is None:
                continue
            dd = (ath - now) / ath * 100
            change = calc_change_percent(now, pa1)

            msg = f"{ticker}: {now:.2f} USD | DD {dd:.2f}% | Δ365 {change:.2f}%"

            if dd >= threshold and last_alerted == 0:
                buf = build_chart(ticker, ath)
                if buf:
                    bot.send_photo(CHAT_ID, buf, caption="⚠️ Просадка!\n\n" + msg)
                else:
                    bot.send_message(CHAT_ID, "⚠️ Просадка!\n\n" + msg)
                c.execute("UPDATE subs SET last_alerted=1, rebound_sent=0 WHERE ticker=? AND chat_id=?", (ticker, CHAT_ID)); db.commit()

            if dd < threshold and rebound_enabled == 1 and last_alerted == 1 and rebound_sent == 0:
                bot.send_message(CHAT_ID, "📈 Rebound!\n\n" + msg)
                c.execute("UPDATE subs SET rebound_sent=1 WHERE ticker=? AND chat_id=?", (ticker, CHAT_ID)); db.commit()

        time.sleep(CHECK_INTERVAL)

# ==== RUN APP ====
application = Application.builder().token(TOKEN).build()

application.add_handler(CommandHandler("start", start_cmd))
application.add_handler(CommandHandler("list", list_cmd))
application.add_handler(CommandHandler("status", status_cmd))
application.add_handler(CommandHandler("threshold", threshold_cmd))
application.add_handler(CommandHandler("rebound", rebound_cmd))
application.add_handler(CommandHandler("remove", remove_cmd))
application.add_handler(CommandHandler("help", help_cmd))
application.add_handler(CommandHandler("commands", commands_cmd))
application.add_handler(CallbackQueryHandler(remove_callback, pattern="^remove:"))

thread = threading.Thread(target=monitor, daemon=True)
thread.start()

print("Bot running…")
application.run_polling()
