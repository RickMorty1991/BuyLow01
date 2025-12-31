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
def get_price_now(ticker):
    df = yf.Ticker(ticker).history(period="1d")
    return float(df["Close"].iloc[-1]) if not df.empty else None

def get_price_1y_ago(ticker):
    df = yf.Ticker(ticker).history(period="1y")
    return float(df["Close"].iloc[0]) if not df.empty else None

def get_ath_1y(ticker):
    df = yf.Ticker(ticker).history(period="1y")
    if df.empty:
        return None, None
    ath = float(df["Close"].max())
    ath_date = df["Close"].idxmax().strftime("%Y-%m-%d")
    return ath, ath_date

def calc_change_percent(now, ago):
    if ago is None or ago == 0:
        return None
    return (now - ago) / ago * 100

def build_chart_bytes(ticker, ath):
    df = yf.Ticker(ticker).history(period="1y")
    hist = df["Close"]
    if hist.empty or ath is None:
        return None
    plt.figure()
    plt.plot(hist)
    plt.axhline(ath)
    plt.title(f"{ticker} | ATH 1Y {ath:.2f} USD")
    buf = io.BytesIO()
    plt.savefig(buf, format="png")
    plt.close()
    buf.seek(0)
    return buf

# ==== MONITOR LOOP ====
def monitor_etfs():
    while True:
        rows = c.execute("SELECT ticker, threshold, rebound_enabled, last_alerted, rebound_sent, price_ago FROM subs WHERE chat_id=?", (CHAT_ID,)).fetchall()

        for ticker, threshold, rebound_enabled, last_alerted, rebound_sent, price_ago in rows:
            now = get_price_now(ticker)
            ath, ath_date = get_ath_1y(ticker)
            ago = get_price_1y_ago(ticker)

            if now is None or ath is None:
                continue

            dd = (ath - now) / ath * 100
            change = calc_change_percent(now, ago)

            msg = f"📊 *{ticker}*\n💰 Ціна зараз: `{now:.2f} USD`\n"
            if ago is not None and change is not None:
                arrow = "📈" if change > 0 else "📉"
                msg += f"{arrow} 365d ago: `{ago:.2f} USD → {now:.2f} USD ({change:.2f}%)`\n"
            else:
                msg += "📆 365d ago: `N/A`\n"
            msg += f"📉 Просадка від ATH 1Y: `{dd:.2f}%`\n📆 ATH 1Y: `{ath:.2f} USD ({ath_date})`"

            if dd >= threshold and last_alerted == 0:
                chart = build_chart_bytes(ticker, ath)
                if chart:
                    bot.send_photo(CHAT_ID, chart, caption="⚠️ Просадка від ATH!\n\n" + msg, parse_mode="Markdown")
                else:
                    bot.send_message(CHAT_ID, "⚠️ Просадка від ATH!\n\n" + msg, parse_mode="Markdown")

                c.execute("UPDATE subs SET last_alerted=1, rebound_sent=0 WHERE ticker=? AND chat_id=?", (ticker, CHAT_ID))
                db.commit()

            if dd < threshold and rebound_enabled == 1 and last_alerted == 1 and rebound_sent == 0:
                bot.send_message(CHAT_ID, "📈 *Rebound — ціна відновилась!*\n\n" + msg, parse_mode="Markdown")
                c.execute("UPDATE subs SET rebound_sent=1 WHERE ticker=? AND chat_id=?", (ticker, CHAT_ID))
                db.commit()

            if dd >= threshold and rebound_sent == 1:
                c.execute("UPDATE subs SET rebound_sent=0 WHERE ticker=? AND chat_id=?", (ticker, CHAT_ID))
                db.commit()

        time.sleep(CHECK_INTERVAL)

# ==== BOT HANDLERS ====
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        ["➕ Add ETF", "📊 Status"],
        ["📌 My ETFs", "🔁 Force Check All"],
        ["📉 Set Threshold", "📈 Toggle Rebound"],
        ["❓ Help", "📌 Commands"]
    ]
    await update.message.reply_text("Вітаю! Оберіть команду 👇", reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True))

async def help_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "📘 *Help Menu*\n\n"
        "➕ Add ETF — додати ETF у моніторинг\n"
        "📌 My ETFs — список ваших ETF + Remove\n"
        "📉 Set Threshold — встановити поріг просадки від ATH 1Y\n"
        "📈 Toggle Rebound — увімк/вимк сигнал відновлення\n"
        "📊 Status — поточні ціни + графіки\n"
        "🔁 Force Check All — перевірка негайно\n"
        "🗑 Remove — видалення через My ETFs"
    )
    await update.message.reply_text(text, parse_mode="Markdown")

async def commands_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "📌 *Команди бота:*\n\n"
        "/start — меню\n"
        "/list — список ETF\n"
        "/status — статус + графіки\n"
        "/threshold SPY 4 — поріг просадки 4%\n"
        "/rebound QQQ OFF — вимкнути Rebound\n"
        "/remove SPY — прибрати ETF\n"
        "/help — help menu"
    )
    await update.message.reply_text(text, parse_mode="Markdown")

async def list_etfs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    rows = c.execute("SELECT ticker FROM subs WHERE chat_id=?", (CHAT_ID,)).fetchall()
    if not rows:
        return await update.message.reply_text("❗ Немає ETF у списку")

    buttons = [[InlineKeyboardButton(f"{t[0]} | 🗑 Remove", callback_data=f"remove:{t[0]}")] for t in rows]
    await update.message.reply_text("📌 *Ваші ETF:* 👇", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(buttons))

async def remove_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    ticker = query.data.split(":")[1].upper()

    c.execute("DELETE FROM subs WHERE ticker=? AND chat_id=?", (ticker, CHAT_ID))
    db.commit()

    await query.message.reply_text(f"🗑 {ticker} видалено зі списку")
    await list_etfs(update, context)

async def status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    rows = c.execute("SELECT ticker FROM subs WHERE chat_id=?", (CHAT_ID,)).fetchall()
    if not rows:
        return await update.message.reply_text("❗ Немає ETF")

    for (ticker,) in rows:
        now = get_price_now(ticker)
        ath, ath_date = get_ath_1y(ticker)
        ago = get_price_1y_ago(ticker)

        change = calc_change_percent(now, ago)
        arrow = "📈" if change and change > 0 else "📉"

        text = f"{ticker}\n💰 Ціна: {now:.2f} USD\n{arrow} Δ365d: {change:.2f}%\nATH1Y: {ath:.2f} ({ath_date})"
        chart = build_chart_bytes(ticker, ath)
        if chart:
            await update.message.reply_photo(chart, caption=text)
        else:
            await update.message.reply_text(text)

    await update.message.reply_text("📊 Status оновлено ✔")

async def force_check(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🔁 Перевіряю всі ETF негайно…")
    threading.Thread(target=monitor_once, daemon=True).start()

def monitor_once():
    for (t,) in c.execute("SELECT ticker FROM subs WHERE chat_id=?", (CHAT_ID,)):
        ath, _ = get_ath_1y(t)
        chart = build_chart_bytes(t, ath)
        if chart:
            bot.send_photo(CHAT_ID, chart, caption=f"🔍 {t} перевірено")

def reply_router(update, context):
    return

# ==== INIT APP ====
application = Application.builder().token(TOKEN).build()

application.add_handler(CommandHandler("start", start))
application.add_handler(CommandHandler("list", list_etfs))
application.add_handler(CommandHandler("status", status_cmd))
application.add_handler(CommandHandler("threshold", threshold_menu))
application.add_handler(CommandHandler("rebound", rebound_toggle_menu))
application.add_handler(CommandHandler("help", help_menu))
application.add_handler(CommandHandler("commands", commands_menu))
application.add_handler(CommandHandler("force", force_check))
application.add_handler(CallbackQueryHandler(remove_handler, pattern="^remove:"))
application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, reply_router))

threading.Thread(target=monitor_etfs, daemon=True).start()
print("Bot running…")
application.run_polling()
