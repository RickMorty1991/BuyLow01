import yfinance as yf
import sqlite3
import time
import threading
import io
import matplotlib.pyplot as plt
from telegram import Bot, Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, ContextTypes, CallbackQueryHandler

# ==== CONFIG ====
TOKEN = "8404794616:AAHiLBLeHrDOZbi7D3maK58AkQpheDLkUQ8"
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
    if now is None or ago is None or ago == 0:
        return None
    return (now - ago) / ago * 100

def build_chart_bytes(ticker, ath):
    try:
        df = yf.Ticker(ticker).history(period="1y", timeout=10)
        hist = df["Close"]
        if hist.empty or ath is None:
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
        return "❗ Немає даних", None

    drawdown = (ath - now) / ath * 100
    year_change = calc_year_change(now, ago)

    msg = (
        f"📊 *{ticker}*\n"
        f"💰 Ціна зараз: `{now:.2f} USD`\n"
        f"📆 52-week ATH: `{ath:.2f} USD ({ath_date})`\n"
        f"📉 Просадка від ATH: `{drawdown:.2f}%`\n"
    )
    if year_change is not None:
        msg += f"{'📈' if year_change>0 else '📉'} Δ365: `{year_change:.2f}%`\n"

    return msg, ath

# ==== DEFAULT SUBS ====
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

# ==== INLINE BUTTON HANDLERS ====
async def show_status_inline(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    ticker = q.data.split(":")[1]
    text, ath = build_status_text(ticker, q.message.chat.id)
    if ath:
        chart = build_chart_bytes(ticker, ath)
        if chart:
            await q.message.reply_photo(chart, caption=text, parse_mode="Markdown")
            return
    await q.message.reply_text(text, parse_mode="Markdown")

async def remove_inline(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    ticker = q.data.split(":")[1]
    with sql_lock:
        c.execute("DELETE FROM subs WHERE ticker=? AND chat_id=?", (ticker, q.message.chat.id))
    db.commit()
    await q.message.reply_text(f"🗑 {ticker} видалено ✔")

async def rebound_toggle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    ticker = q.data.split(":")[1]
    with sql_lock:
        row = c.execute("SELECT rebound_enabled FROM subs WHERE ticker=? AND chat_id=?", (ticker, q.message.chat.id)).fetchone()
        new = 0 if row and row[0] == 1 else 1
        c.execute("UPDATE subs SET rebound_enabled=?, rebound_sent=0 WHERE ticker=? AND chat_id=?", (new, ticker, q.message.chat.id))
    db.commit()
    await q.message.reply_text(f"🔁 {ticker} rebound {'ON' if new else 'OFF'} ✔")

async def threshold_choose(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    ticker = q.data.split(":")[1]
    buttons = [[InlineKeyboardButton(f"{p}%", callback_data=f"threshold_set:{ticker}:{p}")] for p in [1,3,5,7,10,15]]
    await q.message.reply_text("📉 Обери поріг просадки:", reply_markup=InlineKeyboardMarkup(buttons))

async def threshold_set(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    _, ticker, value = q.data.split(":")
    value = float(value)
    with sql_lock:
        c.execute("UPDATE subs SET threshold=?, last_alerted=0, rebound_sent=0 WHERE ticker=? AND chat_id=?", (value, ticker, q.message.chat.id))
    db.commit()
    await q.message.reply_text(f"✔ {ticker} threshold = {value}% ✔")

# ==== BOT COMMANDS ====
async def status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if not context.args:
        return await update.message.reply_text("❗ Формат: /status <ticker>")
    ticker = context.args[0].upper()
    text, ath = build_status_text(ticker, chat_id)
    if not ath:
        return await update.message.reply_text(text)
    chart = build_chart_bytes(ticker, ath)
    if chart:
        await update.message.reply_photo(chart, caption=text, parse_mode="Markdown")
    else:
        await update.message.reply_text(text, parse_mode="Markdown")

async def list_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    with sql_lock:
        rows = c.execute("SELECT ticker,threshold,rebound_enabled FROM subs WHERE chat_id=?", (chat_id,)).fetchall()
    if not rows:
        return await update.message.reply_text("❗ Немає підписок")

    keyboard = []
    for t, th, rb in rows:
        keyboard.append([
            InlineKeyboardButton("📊 Status", callback_data=f"status:{t}"),
            InlineKeyboardButton(f"📉 {th}%", callback_data=f"threshold_choose:{t}"),
            InlineKeyboardButton(f"🔁 {'ON' if rb else 'OFF'}", callback_data=f"rebound:{t}"),
            InlineKeyboardButton("🗑", callback_data=f"remove:{t}")
        ])

    await update.message.reply_text("📌 *Мої ETF:* 👇", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))

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
    with sql_lock:
        c.execute("INSERT OR IGNORE INTO subs(ticker,chat_id,threshold,rebound_enabled,last_alerted,rebound_sent,price_365d_ago) VALUES(?,?,?,?,?,?,?)",
                  (ticker, chat_id, 5.0, 1, 0, 0, ago))
    db.commit()
    await update.message.reply_text(f"✔ Підписано на {ticker}")

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("ℹ️ Використовуй /list для керування, /status <ticker>, /add <ticker>, /remove <ticker>, /threshold <ticker> <value>, /rebound <ticker> ON/OFF")

# ==== ALERT MONITORING ====
def monitor_loop_runner():
    while True:
        with sql_lock:
            rows = c.execute("SELECT ticker,threshold,rebound_enabled,last_alerted,rebound_sent,price_365d_ago,chat_id FROM subs").fetchall()

        for ticker, threshold, rebound_enabled, last_alerted, rebound_sent, price_ago, chat_id in rows:
            now = get_price_now(ticker)
            ath, ath_date = get_ath_52w(ticker)
            if now is None or ath is None:
                continue
            dd = (ath - now) / ath * 100
            if dd >= threshold and last_alerted == 0:
                chart = build_chart_bytes(ticker, ath)
                if chart:
                    bot.send_photo(chat_id, chart, caption="⚠️ Просадка!", parse_mode="Markdown")
                else:
                    bot.send_message(chat_id, "⚠️ Просадка!", parse_mode="Markdown")
                with sql_lock:
                    c.execute("UPDATE subs SET last_alerted=1, rebound_sent=0 WHERE ticker=? AND chat_id=?", (ticker, chat_id))
                db.commit()
        time.sleep(CHECK_INTERVAL)

# ==== RUN ====
def main():
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("list", list_cmd))
    app.add_handler(CommandHandler("add", add_cmd))
    app.add_handler(CommandHandler("status", status_cmd))
    app.add_handler(CommandHandler("help", help_cmd))

    # inline buttons
    app.add_handler(CallbackQueryHandler(show_status_inline, pattern="^status:"))
    app.add_handler(CallbackQueryHandler(remove_inline, pattern="^remove:"))
    app.add_handler(CallbackQueryHandler(threshold_choose, pattern="^threshold_choose:"))
    app.add_handler(CallbackQueryHandler(rebound_toggle, pattern="^rebound:"))
    app.add_handler(CallbackQueryHandler(threshold_set, pattern="^threshold_set:"))

    threading.Thread(target=monitor_loop_runner, daemon=True).start()
    print("Bot running…")
    app.run_polling()

if __name__ == "__main__":
    main()
