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

def monitor_loop():
    while True:
        with sql_lock:
            rows = c.execute("SELECT ticker,threshold,rebound_enabled,last_alerted,rebound_sent,price_365d_ago,chat_id FROM subs").fetchall()

        for ticker, threshold, rebound_enabled, last_alerted, rebound_sent, price_ago, chat_id in rows:
            now = get_price_now(ticker)
            ath, ath_date = get_ath_52w(ticker)
            if now is None or ath is None:
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
                bot.send_message(chat_id, "⚠️ *Просадка від ATH!*\n\n" + msg, parse_mode="Markdown")
                with sql_lock:
                    c.execute("UPDATE subs SET last_alerted=1, rebound_sent=0 WHERE ticker=? AND chat_id=?", (ticker, chat_id))
                db.commit()

            if dd < threshold and rebound_enabled == 1 and last_alerted == 1 and rebound_sent == 0:
                bot.send_message(chat_id, "📈 *Rebound після просадки!*\n\n" + msg, parse_mode="Markdown")
                with sql_lock:
                    c.execute("UPDATE subs SET rebound_sent=1 WHERE ticker=? AND chat_id=?", (ticker, chat_id))
                db.commit()

        time.sleep(CHECK_INTERVAL)

# ==== BOT COMMANDS ====
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    init_defaults(chat_id)

    menu = [
        [InlineKeyboardButton("📌 My ETFs", callback_data="menu:list")],
        [InlineKeyboardButton("➕ Add ETF", callback_data="menu:add")],
        [InlineKeyboardButton("🔁 Force check all", callback_data="menu:check")],
        [InlineKeyboardButton("❓ Help", callback_data="menu:help")]
    ]
    await update.message.reply_text("🤖 *ETF Bot Menu:* 👇", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(menu))

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

    ago_price = get_price_365d_ago(ticker) or now
    msg = f"✔ *{ticker} додано*\n💰 `{now:.2f} USD`\n📆 ATH `{ath:.2f} ({ath_date})`"

    await update.message.reply_text(msg, parse_mode="Markdown")

    with sql_lock:
        c.execute(
            "INSERT OR IGNORE INTO subs(ticker,chat_id,threshold,rebound_enabled,last_alerted,rebound_sent,price_365d_ago) VALUES(?,?,?,?,?,?,?)",
            (ticker, chat_id, 5.0, 1, 0, 0, ago_price)
        )
    db.commit()

async def status_cmd_inline(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    ticker = q.data.split(":")[1]
    text, ath = build_status_text(ticker, q.message.chat.id)
    await q.message.reply_text(text, parse_mode="Markdown")

async def remove_cmd_inline(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    ticker = q.data.split(":")[1]
    with sql_lock:
        c.execute("DELETE FROM subs WHERE ticker=? AND chat_id=?", (ticker, q.message.chat.id))
    db.commit()
    await q.message.reply_text(f"🗑 {ticker} видалено ✔")

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("ℹ️ Використовуй /list, /status <ticker>, /add <ticker>, /remove <ticker>")

# ==== RUN ====
def main():
    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("list", list_cmd))
    app.add_handler(CommandHandler("add", add_cmd))
    app.add_handler(CommandHandler("help", help_cmd))

    app.add_handler(CallbackQueryHandler(status_cmd_inline, pattern="^status:"))
    app.add_handler(CallbackQueryHandler(remove_cmd_inline, pattern="^remove:"))
    app.add_handler(CallbackQueryHandler(threshold_choose, pattern="^threshold_choose:"))
    app.add_handler(CallbackQueryHandler(rebound_toggle, pattern="^rebound:"))

    threading.Thread(target=monitor_loop, daemon=True).start()
    print("Bot running…")
    app.run_polling()

if __name__ == "__main__":
    main()
