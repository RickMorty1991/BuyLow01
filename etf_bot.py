import os
import yfinance as yf
import sqlite3
import time
import threading
import io
import matplotlib.pyplot as plt

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    CallbackQueryHandler,
)

# ================= CONFIG =================
BOT_TOKEN = os.getenv("8404794616:AAG_h14w76Pn6bOCS3Hxokd2ddHIsHcfyDM")  # ❗ ENV
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
def get_price_now(t):
    try:
        df = yf.Ticker(t).history(period="1d")
        return float(df["Close"].iloc[-1]) if not df.empty else None
    except:
        return None

def get_ath_52w(t):
    try:
        df = yf.Ticker(t).history(period="1y")
        if df.empty:
            return None, None
        return float(df["Close"].max()), df.index[df["Close"].argmax()].strftime("%Y-%m-%d")
    except:
        return None, None

def build_chart(ticker, ath):
    try:
        df = yf.Ticker(ticker).history(period="1y")
        if df.empty:
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
    chat_id = update.effective_chat.id
    with lock:
        if not c.execute("SELECT 1 FROM subs WHERE chat_id=?", (chat_id,)).fetchone():
            for t, th in [("SPY", 4), ("QQQ", 7)]:
                c.execute(
                    "INSERT OR IGNORE INTO subs(chat_id,ticker,threshold) VALUES(?,?,?)",
                    (chat_id, t, th),
                )
            db.commit()

    await show_list(update)

async def show_list(update: Update):
    chat_id = update.effective_chat.id
    with lock:
        rows = c.execute(
            "SELECT ticker, threshold, rebound_enabled FROM subs WHERE chat_id=?",
            (chat_id,),
        ).fetchall()

    kb = []
    for t, th, rb in rows:
        kb.append([
            InlineKeyboardButton("📊", callback_data=f"status:{t}"),
            InlineKeyboardButton(f"📉 {th}%", callback_data=f"threshold:{t}"),
            InlineKeyboardButton(f"🔁 {'ON' if rb else 'OFF'}", callback_data=f"rebound:{t}"),
            InlineKeyboardButton("🗑", callback_data=f"remove:{t}")
        ])

    await update.message.reply_text(
        "📌 *My ETFs*",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(kb),
    )

# ================= CALLBACKS =================
async def callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    chat_id = q.message.chat.id
    data = q.data

    if data.startswith("status:"):
        ticker = data.split(":")[1]
        now = get_price_now(ticker)
        ath, ath_date = get_ath_52w(ticker)
        if now is None:
            return await q.message.reply_text("No data")

        text = (
            f"*{ticker}*\n"
            f"💰 {now:.2f} USD\n"
            f"📆 ATH {ath:.2f} ({ath_date})"
        )

        chart = build_chart(ticker, ath)
        if chart:
            await q.message.reply_photo(chart, caption=text, parse_mode="Markdown")
        else:
            await q.message.reply_text(text, parse_mode="Markdown")

    elif data.startswith("remove:"):
        ticker = data.split(":")[1]
        with lock:
            c.execute("DELETE FROM subs WHERE chat_id=? AND ticker=?", (chat_id, ticker))
            db.commit()
        await q.message.reply_text(f"{ticker} removed")
        await show_list(update)

    elif data.startswith("rebound:"):
        ticker = data.split(":")[1]
        with lock:
            row = c.execute(
                "SELECT rebound_enabled FROM subs WHERE chat_id=? AND ticker=?",
                (chat_id, ticker),
            ).fetchone()
            new = 0 if row and row[0] else 1
            c.execute(
                "UPDATE subs SET rebound_enabled=? WHERE chat_id=? AND ticker=?",
                (new, chat_id, ticker),
            )
            db.commit()
        await q.message.reply_text(f"{ticker} rebound {'ON' if new else 'OFF'}")

    elif data.startswith("threshold:"):
        ticker = data.split(":")[1]
        kb = [[InlineKeyboardButton(f"{p}%", callback_data=f"setth:{ticker}:{p}")] for p in [3,5,7,10]]
        await q.message.reply_text("Select threshold:", reply_markup=InlineKeyboardMarkup(kb))

    elif data.startswith("setth:"):
        _, ticker, val = data.split(":")
        with lock:
            c.execute(
                "UPDATE subs SET threshold=?, last_alerted=0 WHERE chat_id=? AND ticker=?",
                (float(val), chat_id, ticker),
            )
            db.commit()
        await q.message.reply_text(f"{ticker} threshold = {val}%")

# ================= MONITOR =================
def monitor_loop(app):
    while True:
        with lock:
            rows = c.execute(
                "SELECT chat_id,ticker,threshold,last_alerted FROM subs"
            ).fetchall()

        for chat_id, ticker, th, alerted in rows:
            try:
                now = get_price_now(ticker)
                ath, _ = get_ath_52w(ticker)
                if now is None or ath is None:
                    continue
                dd = (ath - now) / ath * 100
                if dd >= th and alerted == 0:
                    app.bot.send_message(chat_id, f"⚠️ {ticker} drawdown {dd:.2f}%")
                    with lock:
                        c.execute(
                            "UPDATE subs SET last_alerted=1 WHERE chat_id=? AND ticker=?",
                            (chat_id, ticker),
                        )
                        db.commit()
            except Exception as e:
                print("Monitor error:", e)

        time.sleep(CHECK_INTERVAL)

# ================= RUN =================
def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CallbackQueryHandler(callbacks))
    threading.Thread(target=monitor_loop, args=(app,), daemon=True).start()
    app.run_polling()

if __name__ == "__main__":
    main()
