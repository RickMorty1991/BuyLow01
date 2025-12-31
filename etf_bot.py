import yfinance as yf
import sqlite3
import time
import threading
import io
import logging
import matplotlib.pyplot as plt

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    CallbackQueryHandler,
)

# ================= CONFIG =================
TOKEN = "8404794616:AAHiLBLeHrDOZbi7D3maK58AkQpheDLkUQ8"
CHECK_INTERVAL = 600  # seconds

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("ETF-BOT")

# ================= DATABASE =================
db = sqlite3.connect("etf_bot.db", check_same_thread=False)
c = db.cursor()
lock = threading.Lock()

c.execute("""
CREATE TABLE IF NOT EXISTS subs (
    ticker TEXT,
    chat_id INTEGER,
    threshold REAL DEFAULT 5.0,
    rebound_enabled INTEGER DEFAULT 1,
    last_alerted INTEGER DEFAULT 0,
    rebound_sent INTEGER DEFAULT 0,
    price_365d_ago REAL DEFAULT 0,
    PRIMARY KEY (ticker, chat_id)
)
""")
db.commit()

# ================= HELPERS =================
def yf_safe_history(ticker, period):
    try:
        df = yf.Ticker(ticker).history(period=period)
        return df if not df.empty else None
    except Exception:
        return None

def get_price_now(ticker):
    df = yf_safe_history(ticker, "1d")
    return float(df["Close"].iloc[-1]) if df is not None else None

def get_ath_52w(ticker):
    df = yf_safe_history(ticker, "1y")
    if df is None:
        return None, None
    ath = float(df["Close"].max())
    date = df["Close"].idxmax().strftime("%Y-%m-%d")
    return ath, date

def get_price_365d_ago(ticker):
    df = yf_safe_history(ticker, "1y")
    return float(df["Close"].iloc[0]) if df is not None else None

def calc_change(now, ago):
    if not now or not ago:
        return None
    return (now - ago) / ago * 100

def build_chart(ticker, ath):
    df = yf_safe_history(ticker, "1y")
    if df is None:
        return None

    buf = io.BytesIO()
    plt.figure()
    plt.plot(df["Close"])
    plt.axhline(ath)
    plt.title(f"{ticker} — 52W ATH")
    plt.savefig(buf, format="png")
    plt.close()
    buf.seek(0)
    return buf

# ================= BOT COMMANDS =================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kb = [
        [InlineKeyboardButton("📌 My ETFs", callback_data="menu:list")],
        [InlineKeyboardButton("➕ Add ETF", callback_data="menu:add")],
        [InlineKeyboardButton("❓ Help", callback_data="menu:help")],
    ]
    await update.message.reply_text(
        "🤖 *ETF Monitor Bot*",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(kb),
    )

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "/add <ticker>\n"
        "/list\n"
        "/status <ticker>\n"
        "/threshold <ticker> <value>\n"
        "/rebound <ticker> ON/OFF"
    )

async def add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        return await update.message.reply_text("❗ /add TICKER")

    ticker = context.args[0].upper()
    now = get_price_now(ticker)
    ath, _ = get_ath_52w(ticker)

    if not now or not ath:
        return await update.message.reply_text("❌ Invalid ticker")

    ago = get_price_365d_ago(ticker) or now

    with lock:
        c.execute(
            "INSERT OR IGNORE INTO subs VALUES (?,?,?,?,?,?,?)",
            (ticker, update.effective_chat.id, 5, 1, 0, 0, ago),
        )
        db.commit()

    await update.message.reply_text(f"✔ {ticker} added")

async def list_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    with lock:
        rows = c.execute(
            "SELECT ticker, threshold, rebound_enabled FROM subs WHERE chat_id=?",
            (update.effective_chat.id,),
        ).fetchall()

    if not rows:
        return await update.message.reply_text("❗ No ETFs")

    kb = []
    for t, th, rb in rows:
        kb.append([
            InlineKeyboardButton("📊", callback_data=f"status:{t}"),
            InlineKeyboardButton(f"📉 {th}%", callback_data=f"th:{t}"),
            InlineKeyboardButton("🔁" if rb else "⛔", callback_data=f"rb:{t}"),
            InlineKeyboardButton("🗑", callback_data=f"rm:{t}"),
        ])

    await update.message.reply_text("📌 ETFs:", reply_markup=InlineKeyboardMarkup(kb))

# ================= CALLBACK ROUTER =================
async def callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    chat = q.message.chat.id
    data = q.data

    if data == "menu:list":
        return await list_cmd(update, context)

    if data == "menu:add":
        return await q.message.reply_text("Use /add TICKER")

    if data.startswith("status:"):
        t = data.split(":")[1]
        now = get_price_now(t)
        ath, date = get_ath_52w(t)
        ago = get_price_365d_ago(t)
        dd = (ath - now) / ath * 100
        ch = calc_change(now, ago)

        msg = f"*{t}*\n💰 {now:.2f}\n📉 DD {dd:.2f}%"
        if ch:
            msg += f"\nΔ365 {ch:.2f}%"

        chart = build_chart(t, ath)
        if chart:
            await q.message.reply_photo(chart, caption=msg, parse_mode="Markdown")
        else:
            await q.message.reply_text(msg, parse_mode="Markdown")

    if data.startswith("rm:"):
        t = data.split(":")[1]
        with lock:
            c.execute("DELETE FROM subs WHERE ticker=? AND chat_id=?", (t, chat))
            db.commit()
        await q.message.reply_text(f"🗑 {t} removed")

    if data.startswith("rb:"):
        t = data.split(":")[1]
        with lock:
            row = c.execute(
                "SELECT rebound_enabled FROM subs WHERE ticker=? AND chat_id=?",
                (t, chat),
            ).fetchone()
            new = 0 if row and row[0] else 1
            c.execute(
                "UPDATE subs SET rebound_enabled=?, rebound_sent=0 WHERE ticker=? AND chat_id=?",
                (new, t, chat),
            )
            db.commit()
        await q.message.reply_text(f"🔁 {t} {'ON' if new else 'OFF'}")

# ================= MONITOR =================
def monitor(app: Application):
    while True:
        with lock:
            rows = c.execute("SELECT * FROM subs").fetchall()

        for t, chat, th, rb, alerted, sent, ago in rows:
            now = get_price_now(t)
            ath, _ = get_ath_52w(t)
            if not now or not ath:
                continue

            dd = (ath - now) / ath * 100

            if dd >= th and alerted == 0:
                app.bot.send_message(chat, f"⚠️ {t} DD {dd:.2f}%")
                with lock:
                    c.execute(
                        "UPDATE subs SET last_alerted=1 WHERE ticker=? AND chat_id=?",
                        (t, chat),
                    )
                    db.commit()

        time.sleep(CHECK_INTERVAL)

# ================= MAIN =================
def main():
    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("add", add))
    app.add_handler(CommandHandler("list", list_cmd))
    app.add_handler(CallbackQueryHandler(callbacks))

    threading.Thread(target=monitor, args=(app,), daemon=True).start()
    log.info("Bot started")
    app.run_polling()

if __name__ == "__main__":
    main()
