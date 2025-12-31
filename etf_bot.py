import yfinance as yf
import sqlite3
import time
import threading
import io
import matplotlib.pyplot as plt
from telegram import Bot, Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters, CallbackQueryHandler

# ==== CONFIG ====
TOKEN = "8404794616:AAHUJeJp_wvOa8poUXcZufJRXXC72pZZgU0"
CHAT_ID = 409544912
CHECK_INTERVAL = 600  # 10 хв

# ==== DATABASE ====
db = sqlite3.connect("etf_bot.db", check_same_thread=False)
c = db.cursor()
c.execute("""
CREATE TABLE IF NOT EXISTS subs(
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

bot = Bot(TOKEN)
sql_lock = threading.Lock()

# ==== FINANCE HELPERS ====
def get_price_now(ticker: str):
    try:
        df = yf.Ticker(ticker).history(period="1d", timeout=10)
        if df.empty:
            return None
        return float(df["Close"].iloc[-1])
    except Exception:
        return None

def get_ath_52w(ticker: str):
    try:
        df = yf.Ticker(ticker).history(period="1y", timeout=10)
        if df.empty:
            return None, None
        ath = float(df["Close"].max())
        ath_date = df.index[df["Close"].argmax()].strftime("%Y-%m-%d")
        return ath, ath_date
    except Exception:
        return None, None

def get_price_365d_ago(ticker: str):
    try:
        df = yf.Ticker(ticker).history(period="1y", timeout=10)
        if df.empty:
            return None
        return float(df["Close"].iloc[0])
    except Exception:
        return None

def calc_year_change(now: float, ago: float):
    if ago is None or ago == 0 or now is None:
        return None
    return (now - ago) / ago * 100

def build_chart_bytes(ticker: str, ath: float):
    try:
        df = yf.Ticker(ticker).history(period="1y", timeout=10)
        hist = df["Close"]
        if hist.empty or ath is None:
            return None
        plt.figure()
        plt.plot(hist)
        plt.axhline(ath)
        plt.title(f"{ticker} | 52-week ATH: {ath:.2f} USD")
        buf = io.BytesIO()
        plt.savefig(buf, format="png")
        plt.close()
        buf.seek(0)
        return buf
    except Exception:
        return None

# ==== DEFAULT SUBS INIT ====
def init_defaults(chat_id: int):
    with sql_lock:
        rows = c.execute("SELECT ticker FROM subs WHERE chat_id=?", (chat_id,)).fetchall()
        if rows:
            return
        for t, th in [("SPY", 4.0), ("QQQ", 7.0)]:
            now = get_price_now(t)
            if now is None:
                continue
            ago = get_price_365d_ago(t) or now
            c.execute(
                "INSERT OR IGNORE INTO subs(ticker,chat_id,threshold,rebound_enabled,last_alerted,rebound_sent,price_365d_ago) VALUES(?,?,?,?,?,?,?)",
                (t, chat_id, th, 1, 0, 0, ago)
            )
    db.commit()

# ==== MONITOR THREAD ====
def monitor_loop():
    while True:
        with sql_lock:
            rows = c.execute(
                "SELECT ticker,threshold,rebound_enabled,last_alerted,rebound_sent,price_365d_ago,chat_id FROM subs"
            ).fetchall()

        for ticker, threshold, rebound_enabled, last_alerted, rebound_sent, price_ago, chat_id in rows:
            try:
                now = get_price_now(ticker)
                ath, ath_date = get_ath_52w(ticker)
                if now is None or ath is None:
                    continue

                dd = (ath - now) / ath * 100
                yc = calc_year_change(now, price_ago)

                msg = (
                    f"📊 *{ticker}*\n"
                    f"💰 Ціна зараз: `{now:.2f} USD`\n"
                    f"📆 52-week ATH: `{ath:.2f} USD ({ath_date})`\n"
                    f"📉 Просадка від ATH: `{dd:.2f}%`"
                )
                if yc is not None:
                    arrow = "📈" if yc > 0 else "📉"
                    msg += f"\n{arrow} Δ365: `{yc:.2f}%`"

                # Alert просадки
                if dd >= threshold and last_alerted == 0:
                    chart = build_chart_bytes(ticker, ath)
                    if chart:
                        bot.send_photo(chat_id, chart, caption="⚠️ *Просадка від ATH!*\n\n" + msg, parse_mode="Markdown")
                    else:
                        bot.send_message(chat_id, "⚠️ *Просадка від ATH!*\n\n" + msg, parse_mode="Markdown")

                    with sql_lock:
                        c.execute("UPDATE subs SET last_alerted=1, rebound_sent=0 WHERE ticker=? AND chat_id=?", (ticker, chat_id))
                    db.commit()

                # Alert відскоку
                if dd < threshold and rebound_enabled == 1 and last_alerted == 1 and rebound_sent == 0:
                    bot.send_message(chat_id, "📈 *Rebound після просадки!*\n\n" + msg, parse_mode="Markdown")
                    with sql_lock:
                        c.execute("UPDATE subs SET rebound_sent=1 WHERE ticker=? AND chat_id=?", (ticker, chat_id))
                    db.commit()

                # Reset rebound flag якщо впало знову
                if dd >= threshold and rebound_sent == 1:
                    with sql_lock:
                        c.execute("UPDATE subs SET rebound_sent=0 WHERE ticker=? AND chat_id=?", (ticker, chat_id))
                    db.commit()

            except Exception as e:
                print(f"[Thread ETF Error] {ticker}: {e}")
                continue

        time.sleep(CHECK_INTERVAL)

# ==== COMMAND HANDLERS ====
async def status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if not context.args:
        return await bot.send_message(chat_id, "❗ Формат: /status <ticker>")
    ticker = context.args[0].upper()
    text, ath = build_status_text(ticker)
    if not text:
        return await bot.send_message(chat_id, "❗ Немає даних")
    ch = build_chart_bytes(ticker, ath)
    if ch:
        await bot.send_photo(chat_id, ch, caption=text, parse_mode="Markdown")
    else:
        await bot.send_message(chat_id, text, parse_mode="Markdown")

async def add_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if not context.args:
        return await bot.send_message(chat_id, "❗ Формат: /add <ticker>")
    ticker = context.args[0].upper()
    now = get_price_now(ticker)
    ath, ath_date = get_ath_52w(ticker)
    if now is None or ath is None:
        return await bot.send_message(chat_id, "❗ Невалідний тікер або немає даних")
    ago_price = get_price_365d_ago(ticker) or now
    yc = calc_year_change(now, ago_price)
    msg = f"✔ *{ticker} додано*\n💰 `{now:.2f} USD`\n📆 ATH `{ath:.2f} ({ath_date})`"
    if yc is not None:
        msg += f"\nΔ365 `{yc:.2f}%`"
    ch = build_chart_bytes(ticker, ath)
    if ch:
        await bot.send_photo(chat_id, ch, caption=msg, parse_mode="Markdown")
    else:
        await bot.send_message(chat_id, msg, parse_mode="Markdown")
    with sql_lock:
        c.execute("INSERT OR IGNORE INTO subs(ticker,chat_id,threshold,rebound_enabled,last_alerted,rebound_sent,price_365d_ago) VALUES(?,?,?,?,?,?,?)",
                  (ticker, chat_id, 5.0, 1, 0, 0, ago_price))
    db.commit()
    await bot.send_message(chat_id, f"✔ Підписано на {ticker}")

async def remove_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if not context.args:
        return await bot.send_message(chat_id, "❗ Формат: /remove <ticker>")
    ticker = context.args[0].upper()
    with sql_lock:
        c.execute("DELETE FROM subs WHERE ticker=? AND chat_id=?", (ticker, chat_id))
    db.commit()
    await bot.send_message(chat_id, f"🗑 {ticker} видалено ✔")

async def threshold_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if len(context.args) < 2:
        return await bot.send_message(chat_id, "❗ Формат: /threshold <ticker> <value>")
    ticker, value = context.args[0].upper(), float(context.args[1])
    with sql_lock:
        c.execute("UPDATE subs SET threshold=?,last_alerted=0,rebound_sent=0 WHERE ticker=? AND chat_id=?", (value, ticker, chat_id))
    db.commit()
    await bot.send_message(chat_id, f"✔ Поріг {ticker} = {value}% ✔")

async def rebound_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if len(context.args) < 2:
        return await bot.send_message(chat_id, "❗ Формат: /rebound <ticker> ON/OFF")
    ticker, state = context.args[0].upper(), context.args[1].upper()
    new = 1 if state == "ON" else 0
    with sql_lock:
        c.execute("UPDATE subs SET rebound_enabled=?,rebound_sent=0 WHERE ticker=? AND chat_id=?", (new, ticker, chat_id))
    db.commit()
    await bot.send_message(chat_id, f"🔁 Rebound {ticker}: {state} ✔", parse_mode="Markdown")

async def check_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    await bot.send_message(chat_id, "🔁 Перевіряю всі ETF…")
    with sql_lock:
        rows = c.execute("SELECT ticker FROM subs WHERE chat_id=?", (chat_id,)).fetchall()
    for (t,) in rows:
        text, ath = build_status_text(t)
        if text:
            await bot.send_message(chat_id, text, parse_mode="Markdown")

async def commands_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await bot.send_message(
        update.effective_chat.id,
        "ℹ️ Команди: /start, /list, /status <ticker>, /add <ticker>, /remove <ticker>, /threshold <ticker> <value>, /rebound <ticker> ON/OFF, /check, /help"
    )

# ==== START BOT ====
def main():
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("list", list_cmd))
    app.add_handler(CommandHandler("status", status_cmd))
    app.add_handler(CommandHandler("add", add_cmd))
    app.add_handler(CommandHandler("remove", remove_cmd))
    app.add_handler(CommandHandler("threshold", threshold_cmd))
    app.add_handler(CommandHandler("rebound", rebound_cmd))
    app.add_handler(CommandHandler("check", check_cmd))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("commands", commands_cmd))
    app.add_handler(CallbackQueryHandler(inline_router))
    threading.Thread(target=monitor_loop, daemon=True).start()
    print("Bot running…")
    app.run_polling()

if __name__ == "__main__":
    main()
