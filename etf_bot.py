import yfinance as yf
import sqlite3
import time
import threading
import io
import matplotlib.pyplot as plt
from datetime import datetime, timedelta
from threading import Lock

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
)

# ================== CONFIG ==================
TELEGRAM_TOKEN = "8404794616:AAHiLBLeHrDOZbi7D3maK58AkQpheDLkUQ8"
CHECK_INTERVAL = 900  # 15 min
DB_PATH = "etf.db"

lock = Lock()

# ================== DB ==================
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

# ================== PRICE HELPERS ==================
def get_price_now(t):
    try:
        return yf.Ticker(t).history(period="1d")["Close"][-1]
    except:
        return None

def get_price_365d_ago(t):
    try:
        h = yf.Ticker(t).history(period="1y")
        return h["Close"][0]
    except:
        return None

def get_ath_52w(t):
    try:
        h = yf.Ticker(t).history(period="1y")
        idx = h["Close"].idxmax()
        return h["Close"].max(), idx.date()
    except:
        return None, None

def calc_change(now, ago):
    if now and ago:
        return (now - ago) / ago * 100
    return None

def build_chart(ticker, ath):
    try:
        h = yf.Ticker(ticker).history(period="1y")
        plt.figure(figsize=(6, 3))
        plt.plot(h.index, h["Close"])
        plt.axhline(ath, linestyle="--")
        plt.title(ticker)
        buf = io.BytesIO()
        plt.tight_layout()
        plt.savefig(buf, format="png")
        plt.close()
        buf.seek(0)
        return buf
    except:
        return None

# ================== MONITOR LOOP ==================
def monitor_loop(app: Application):
    while True:
        with lock:
            rows = c.execute(
                "SELECT chat_id, ticker, threshold, rebound_enabled, last_alerted, rebound_sent "
                "FROM subs"
            ).fetchall()

        for chat_id, t, th, rb, la, rs in rows:
            now = get_price_now(t)
            ath, _ = get_ath_52w(t)
            if not now or not ath:
                continue

            dd = (ath - now) / ath * 100

            # ALERT
            if dd >= th and la == 0:
                app.bot.send_message(
                    chat_id,
                    f"⚠️ *{t}*\n📉 Drawdown {dd:.2f}%",
                    parse_mode="Markdown",
                )
                with lock:
                    c.execute(
                        "UPDATE subs SET last_alerted=1, rebound_sent=0 WHERE chat_id=? AND ticker=?",
                        (chat_id, t),
                    )
                    db.commit()

            # REBOUND
            if dd < th and rb and la and not rs:
                app.bot.send_message(
                    chat_id,
                    f"📈 *{t}* rebound\nDD {dd:.2f}%",
                    parse_mode="Markdown",
                )
                with lock:
                    c.execute(
                        "UPDATE subs SET rebound_sent=1 WHERE chat_id=? AND ticker=?",
                        (chat_id, t),
                    )
                    db.commit()

            # RESET
            if dd >= th and rs:
                with lock:
                    c.execute(
                        "UPDATE subs SET rebound_sent=0 WHERE chat_id=? AND ticker=?",
                        (chat_id, t),
                    )
                    db.commit()

        time.sleep(CHECK_INTERVAL)

# ================== COMMANDS ==================
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kb = [
        [InlineKeyboardButton("📌 My ETFs", callback_data="menu:list")],
        [InlineKeyboardButton("➕ Add ETF", callback_data="menu:add")],
        [InlineKeyboardButton("❓ Help", callback_data="menu:help")],
    ]
    await update.message.reply_text(
        "ETF Monitor Bot",
        reply_markup=InlineKeyboardMarkup(kb),
    )

async def add_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        return await update.message.reply_text("Use: /add TICKER")

    t = context.args[0].upper()
    with lock:
        c.execute(
            "INSERT OR IGNORE INTO subs(chat_id, ticker) VALUES(?,?)",
            (update.message.chat.id, t),
        )
        db.commit()
    await update.message.reply_text(f"✔ {t} added")

# ================== CALLBACK ROUTER ==================
async def callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    chat_id = q.message.chat.id
    data = q.data

    # MENU
    if data == "menu:list":
        with lock:
            rows = c.execute(
                "SELECT ticker, threshold, rebound_enabled FROM subs WHERE chat_id=?",
                (chat_id,),
            ).fetchall()

        if not rows:
            return await q.message.reply_text("❗ No ETFs")

        kb = []
        for t, th, rb in rows:
            kb.append([
                InlineKeyboardButton("📊", callback_data=f"st:{t}"),
                InlineKeyboardButton(f"📉 {th}%", callback_data=f"th:{t}"),
                InlineKeyboardButton("🔁 ON" if rb else "🔁 OFF", callback_data=f"rb:{t}"),
                InlineKeyboardButton("🗑", callback_data=f"rm:{t}"),
            ])

        return await q.message.reply_text(
            "📌 *My ETFs*",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(kb),
        )

    if data == "menu:add":
        return await q.message.reply_text("Use: /add TICKER")

    if data == "menu:help":
        return await q.message.reply_text(
            "/start\n/add TICKER\n\nButtons control everything"
        )

    # STATUS
    if data.startswith("st:"):
        t = data.split(":")[1]
        now = get_price_now(t)
        ath, date = get_ath_52w(t)
        ago = get_price_365d_ago(t)
        dd = (ath - now) / ath * 100
        ch = calc_change(now, ago)

        msg = (
            f"*{t}*\n"
            f"💰 {now:.2f} USD\n"
            f"📉 DD {dd:.2f}%\n"
            f"📆 ATH {ath:.2f} ({date})"
        )
        if ch is not None:
            msg += f"\nΔ365 {ch:.2f}%"

        chart = build_chart(t, ath)
        if chart:
            return await q.message.reply_photo(chart, caption=msg, parse_mode="Markdown")
        return await q.message.reply_text(msg, parse_mode="Markdown")

    # REMOVE
    if data.startswith("rm:"):
        t = data.split(":")[1]
        with lock:
            c.execute(
                "DELETE FROM subs WHERE chat_id=? AND ticker=?",
                (chat_id, t),
            )
            db.commit()
        return await q.message.reply_text(f"🗑 {t} removed")

    # REBOUND
    if data.startswith("rb:"):
        t = data.split(":")[1]
        with lock:
            cur = c.execute(
                "SELECT rebound_enabled FROM subs WHERE chat_id=? AND ticker=?",
                (chat_id, t),
            ).fetchone()
            new = 0 if cur and cur[0] else 1
            c.execute(
                "UPDATE subs SET rebound_enabled=?, rebound_sent=0 WHERE chat_id=? AND ticker=?",
                (new, chat_id, t),
            )
            db.commit()
        return await q.message.reply_text(f"🔁 {t} {'ON' if new else 'OFF'}")

    # THRESHOLD
    if data.startswith("th:"):
        t = data.split(":")[1]
        kb = [
            [InlineKeyboardButton(p + "%", callback_data=f"ths:{t}:{p}")]
            for p in ["3", "5", "7", "10"]
        ]
        return await q.message.reply_text(
            f"📉 Threshold for {t}",
            reply_markup=InlineKeyboardMarkup(kb),
        )

    if data.startswith("ths:"):
        _, t, val = data.split(":")
        with lock:
            c.execute(
                "UPDATE subs SET threshold=?, last_alerted=0 WHERE chat_id=? AND ticker=?",
                (float(val), chat_id, t),
            )
            db.commit()
        return await q.message.reply_text(f"✔ {t} = {val}%")

# ================== MAIN ==================
def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("add", add_cmd))
    app.add_handler(CallbackQueryHandler(callbacks))

    threading.Thread(
        target=monitor_loop,
        args=(app,),
        daemon=True,
    ).start()

    print("Bot running...")
    app.run_polling()

if __name__ == "__main__":
    main()
