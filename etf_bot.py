import yfinance as yf
import sqlite3
import time
import threading
import io
import matplotlib.pyplot as plt
from telegram import Bot, Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, ContextTypes, CallbackQueryHandler

# ==== CONFIG ====
TOKEN = "8404794616:AAHiLBLeHrDOZbi7D3maK58AkQpheDLkUQ8"  # Підстав свій токен тут
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

# ==== BOT ====
bot = Bot(TOKEN)
sql_lock = threading.Lock()

# ==== HELPERS ====
def get_price_now(ticker: str):
    try:
        df = yf.Ticker(ticker).history(period="1d", timeout=10)
        if df.empty:
            return None
        return float(df["Close"].iloc[-1])
    except:
        return None

def get_ath_52w(ticker: str):
    try:
        df = yf.Ticker(ticker).history(period="1y", timeout=10)
        if df.empty:
            return None, None
        ath = float(df["Close"].max())
        ath_date = df.index[df["Close"].argmax()].strftime("%Y-%m-%d")
        return ath, ath_date
    except:
        return None, None

def get_price_365d_ago(ticker: str):
    try:
        df = yf.Ticker(ticker).history(period="1y", timeout=10)
        if df.empty:
            return None
        return float(df["Close"].iloc[0])
    except:
        return None

def calc_year_change(now, ago):
    if not now or not ago or ago == 0:
        return None
    return (now - ago) / ago * 100

def build_chart_bytes(ticker, ath):
    try:
        df = yf.Ticker(ticker).history(period="1y", timeout=10)
        hist = df["Close"]
        if hist.empty or not ath:
            return None
        plt.figure()
        plt.plot(hist)
        plt.axhline(ath)
        plt.title(f"{ticker} | 52W ATH {ath:.2f} USD")
        buf = io.BytesIO()
        plt.savefig(buf, format="png")
        plt.close()
        buf.seek(0)
        return buf
    except:
        return None

def build_status_text(ticker, chat_id):
    now = get_price_now(ticker)
    ath, ath_date = get_ath_52w(ticker)
    with sql_lock:
        row = c.execute("SELECT price_365d_ago, threshold, rebound_enabled FROM subs WHERE ticker=? AND chat_id=?", (ticker, chat_id)).fetchone()
    ago = float(row[0]) if row else None

    if now is None or ath is None:
        return None, None

    dd = (ath - now) / ath * 100
    yc = calc_year_change(now, ago)

    msg = (
        f"📊 *{ticker}*\n"
        f"💰 Ціна зараз: `{now:.2f} USD`\n"
        f"📆 52W ATH: `{ath:.2f} USD ({ath_date})`\n"
        f"📉 Просадка: `{dd:.2f}%`\n"
    )
    if yc is not None:
        msg += f"{'📈' if yc>0 else '📉'} Δ365: `{yc:.2f}%`\n"

    return msg, ath

# ==== MONITOR THREAD ====
def monitor_loop_runner():
    while True:
        with sql_lock:
            rows = c.execute("SELECT ticker,threshold,rebound_enabled,last_alerted,rebound_sent,price_365d_ago,chat_id FROM subs").fetchall()

        for ticker, threshold, rebound_enabled, last_alerted, rebound_sent, price_ago, chat_id in rows:
            try:
                now = get_price_now(ticker)
                ath, ath_date = get_ath_52w(ticker)
                if not now or not ath:
                    continue

                dd = (ath - now) / ath * 100
                yc = calc_year_change(now, price_ago)

                msg = (
                    f"📊 *{ticker}*\n"
                    f"💰 Ціна: `{now:.2f} USD`\n"
                    f"📆 52W ATH: `{ath:.2f} ({ath_date})`\n"
                    f"📉 Просадка: `{dd:.2f}%`"
                )
                if yc is not None:
                    msg += f"\n{'📈' if yc>0 else '📉'} Δ365: `{yc:.2f}%`"

                if dd >= threshold and last_alerted == 0:
                    chart = build_chart_bytes(ticker, ath)
                    if chart:
                        bot.send_photo(chat_id, chart, caption="⚠️ *Просадка від ATH!*\n\n" + msg, parse_mode="Markdown")
                    else:
                        bot.send_message(chat_id, "⚠️ *Просадка від ATH!*\n\n" + msg, parse_mode="Markdown")
                    with sql_lock:
                        c.execute("UPDATE subs SET last_alerted=1, rebound_sent=0 WHERE ticker=? AND chat_id=?", (ticker, chat_id))
                    db.commit()

                if dd < threshold and rebound_enabled == 1 and last_alerted == 1 and rebound_sent == 0:
                    bot.send_message(chat_id, "📈 *Rebound після просадки!*\n\n" + msg, parse_mode="Markdown")
                    with sql_lock:
                        c.execute("UPDATE subs SET rebound_sent=1 WHERE ticker=? AND chat_id=?", (ticker, chat_id))
                    db.commit()

                if dd >= threshold and rebound_sent == 1:
                    with sql_lock:
                        c.execute("UPDATE subs SET rebound_sent=0 WHERE ticker=? AND chat_id=?", (ticker, chat_id))
                    db.commit()

            except Exception as e:
                print(f"[ETF Monitor Error] {ticker}: {e}")

        time.sleep(CHECK_INTERVAL)

# ==== COMMANDS ====
async def status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if not context.args:
        return await update.message.reply_text("❗ Формат: /status <ticker>")
    ticker = context.args[0].upper()

    text, ath = build_status_text(ticker, chat_id)
    if not text:
        return await update.message.reply_text("❗ Немає даних")

    chart = build_chart_bytes(ticker, ath)
    if chart:
        await bot.send_photo(chat_id, chart, caption=text, parse_mode="Markdown")
    else:
        await bot.send_message(chat_id, text, parse_mode="Markdown")

async def add_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if not context.args:
        return await update.message.reply_text("❗ Формат: /add <ticker>")
    ticker = context.args[0].upper()

    now = get_price_now(ticker)
    ath, ath_date = get_ath_52w(ticker)
    if now is None or ath is None:
        return await update.message.reply_text("❗ Невалідний тікер або немає даних")

    ago_price = get_price_365d_ago(ticker) or now
    msg = f"✔ *{ticker} додано*\n💰 `{now:.2f} USD`\n📆 ATH `{ath:.2f} ({ath_date})`"

    chart = build_chart_bytes(ticker, ath)
    if chart:
        await bot.send_photo(chat_id, chart, caption=msg, parse_mode="Markdown")
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
        return await update.message.reply_text("❗ Формат: /remove <ticker>")
    ticker = context.args[0].upper()
    with sql_lock:
        c.execute("DELETE FROM subs WHERE ticker=? AND chat_id=?", (ticker, chat_id))
    db.commit()
    await bot.send_message(chat_id, f"🗑 {ticker} видалено ✔")

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    await bot.send_message(chat_id, "ℹ️ Використовуй /list для керування ETF, /status <ticker> для перевірки, /add <ticker> щоб підписатись.")

async def list_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    keyboard = [[InlineKeyboardButton("📊 Status SPY", callback_data="status:SPY")]]
    await bot.send_message(chat_id, "📌 My ETFs", reply_markup=InlineKeyboardMarkup(keyboard))

# ==== REGISTER & RUN ====
def main():
    app = Application.builder().token("TOKEN_HERE").build()
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("list", list_cmd))
    app.add_handler(CommandHandler("status", status_single_cmd))
    app.add_handler(CommandHandler("add", add_cmd))
    app.add_handler(CommandHandler("remove", remove_cmd))
    app.add_handler(CommandHandler("help", help_menu))
    app.add_handler(CommandHandler("commands", commands_cmd))
    app.add_handler(CallbackQueryHandler(inline_router))

    threading.Thread(target=monitor_loop_runner, daemon=True).start()
    print("Bot running…")
    app.run_polling()

if __name__ == "__main__":
    main()
