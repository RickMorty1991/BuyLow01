import os
import yfinance as yf
import sqlite3
import time
import threading
import io
import matplotlib.pyplot as plt
from datetime import datetime, timedelta

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, ContextTypes, CallbackQueryHandler

# ================= CONFIG =================
BOT_TOKEN = os.getenv("TELEGRAM_TOKEN")  # ⬅ токен має підтягуватись з ENV у Render
CHECK_INTERVAL = 900
DB_PATH = "etf.db"

lock = threading.Lock()

# ================= DB =================
db = sqlite3.connect(DB_PATH, check_same_thread=False)
c = db.cursor()
c.execute("""
CREATE TABLE IF NOT EXISTS subs (
    chat_id INTEGER,
    ticker TEXT,
    threshold REAL DEFAULT 5,
    rebound_enabled INTEGER DEFAULT 1,
    last_alerted INTEGER DEFAULT 0,
    rebound_sent INTEGER DEFAULT 0,
    PRIMARY KEY (chat_id, ticker)
)
""")
db.commit()

# ================= HELPERS =================
def get_price_now(ticker):
    try:
        df = yf.Ticker(ticker).history(period="1d")
        return float(df["Close"].iloc[-1]) if not df.empty else None
    except:
        return None

def get_price_365d_ago(ticker):
    try:
        df = yf.Ticker(ticker).history(period="1y")
        return float(df["Close"].iloc[0]) if not df.empty else None
    except:
        return None

def get_ath_52w(ticker):
    try:
        df = yf.Ticker(ticker).history(period="1y")
        if df.empty:
            return None, None
        ath = float(df["Close"].max())
        ath_date = df.index[df["Close"].argmax()].strftime("%Y-%m-%d")
        return ath, ath_date
    except:
        return None, None

def calc_change(now, ago):
    if now is None or ago is None or ago == 0:
        return None
    return (now - ago) / ago * 100

def build_chart(ticker, ath):
    try:
        df = yf.Ticker(ticker).history(period="1y")
        if df.empty or ath is None:
            return None
        buf = io.BytesIO()
        plt.figure(figsize=(6, 3))
        plt.plot(df.index, df["Close"])
        plt.axhline(ath, linestyle="--")
        plt.title(ticker)
        plt.tight_layout()
        plt.savefig(buf, format="png")
        plt.close()
        buf.seek(0)
        return buf
    except:
        return None

# ================= COMMANDS =================
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("ETF Monitor Bot ready. Use /list or ➕ to add ETFs.")

async def list_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    with lock:
        rows = c.execute("SELECT ticker, threshold, rebound_enabled FROM subs WHERE chat_id=?", (chat_id,)).fetchall()

    if not rows:
        return await update.message.reply_text("❗ Немає підписок")

    kb = []
    for ticker, threshold, rebound_enabled in rows:
        kb.append([
            InlineKeyboardButton("📊 Status", callback_data=f"status:{ticker}"),
            InlineKeyboardButton(f"📉 {threshold}%", callback_data=f"threshold:{ticker}"),
            InlineKeyboardButton(f"🔁 {'ON' if rebound_enabled else 'OFF'}", callback_data=f"rebound:{ticker}"),
            InlineKeyboardButton("🗑 Remove", callback_data=f"remove:{ticker}")
        ])

    await update.message.reply_text("📌 *My ETFs:* 👇", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))

async def add_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if not context.args:
        return await update.message.reply_text("❗ Формат: /add <ticker>")

    ticker = context.args[0].upper()
    now = get_price_now(ticker)
    ath, ath_date = get_ath_52w(ticker)
    if now is None or ath is None:
        return await update.message.reply_text("❗ Невалідний тікер або немає даних")

    ago = get_price_365d_ago(ticker) or now
    with lock:
        c.execute(
            "INSERT OR IGNORE INTO subs(chat_id, ticker, threshold, rebound_enabled, last_alerted, rebound_sent) VALUES(?,?,?,?,?,?)",
            (chat_id, ticker, 5.0, 1, 0, 0)
        )
        db.commit()

    await update.message.reply_text(f"✔ {ticker} додано")

async def status_inline(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    chat_id = q.message.chat.id
    ticker = q.data.split(":")[1]

    now = get_price_now(ticker)
    ath, ath_date = get_ath_52w(ticker)
    ago = get_price_365d_ago(ticker)
    if now is None or ath is None:
        return await q.message.reply_text("❗ Немає даних")

    dd = (ath - now) / ath * 100
    ch = calc_change(now, ago)

    msg = (
        f"*{ticker}*\n"
        f"💰 {now:.2f} USD\n"
        f"📆 ATH {ath:.2f} ({ath_date})\n"
        f"📉 DD {dd:.2f}%"
    )
    if ch is not None:
        msg += f"\nΔ365 {ch:.2f}%"

    chart = build_chart(ticker, ath)
    if chart:
        await q.message.reply_photo(chart, caption=msg, parse_mode="Markdown")
    else:
        await q.message.reply_text(msg, parse_mode="Markdown")

async def remove_inline(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    chat_id = q.message.chat.id
    ticker = q.data.split(":")[1]
    with lock:
        c.execute("DELETE FROM subs WHERE chat_id=? AND ticker=?", (chat_id, ticker))
        db.commit()
    await q.message.reply_text(f"🗑 {ticker} removed")

# ================= MONITOR =================
def monitor_loop(app: Application):
    while True:
        with lock:
            rows = c.execute("SELECT chat_id, ticker, threshold, last_alerted FROM subs").fetchall()

        for chat_id, ticker, threshold, last_alerted in rows:
            try:
                now = get_price_now(ticker)
                ath, _ = get_ath_52w(ticker)
                if now is None or ath is None:
                    continue
                dd = (ath - now) / ath * 100
                if dd >= threshold and last_alerted == 0:
                    app.bot.send_message(chat_id, f"⚠️ {ticker} drawdown {dd:.2f}%")
                    c.execute("UPDATE subs SET last_alerted=1 WHERE chat_id=? AND ticker=?", (chat_id, ticker))
                    db.commit()
            except:
                pass

        time.sleep(CHECK_INTERVAL)

# ================= RUN =================
def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("list", list_cmd))
    app.add_handler(CommandHandler("add", add_cmd))
    app.add_handler(CallbackQueryHandler(status_inline, pattern="^status:"))
    app.add_handler(CallbackQueryHandler(remove_inline, pattern="^remove:"))

    threading.Thread(target=monitor_loop, args=(app,), daemon=True).start()
    print("Bot running...")
    app.run_polling()

if __name__ == "__main__":
    main()
