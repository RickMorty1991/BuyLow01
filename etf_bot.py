import yfinance as yf
import sqlite3
import time
import threading
import io
import matplotlib.pyplot as plt
from telegram import Bot, Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters, CallbackQueryHandler

# ==== CONFIG ====
TOKEN = "8404794616:AAHiLBLeHrDOZbi7D3maK58AkQpheDLkUQ8"
CHECK_INTERVAL = 600  # 10 хв (інтервал перевірки)

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

def build_status_text(ticker: str, chat_id: int):
    now = get_price_now(ticker)
    ath, ath_date = get_ath_52w(ticker)
    with sql_lock:
        row = c.execute("SELECT price_365d_ago, threshold, rebound_enabled FROM subs WHERE ticker=? AND chat_id=?", (ticker, chat_id)).fetchone()

    ago_price = float(row[0]) if row and row[0] else None

    if now is None or ath is None:
        return None, None

    drawdown = (ath - now) / ath * 100
    year_change = calc_year_change(now, ago_price)

    msg = (
        f"📊 *{ticker}*\n"
        f"💰 Ціна зараз: `{now:.2f} USD`\n"
        f"📆 52-week ATH: `{ath:.2f} USD ({ath_date})`\n"
        f"📉 Просадка від ATH: `{drawdown:.2f}%`\n"
    )
    if year_change is not None:
        arrow = "📈" if year_change > 0 else "📉"
        msg += f"{arrow} Δ365: `{year_change:.2f}%`\n"

    return msg, ath

# ==== MONITORING THREAD ====
def monitor_loop_runner():
    while True:
        with sql_lock:
            rows = c.execute("SELECT ticker, threshold, rebound_enabled, last_alerted, rebound_sent, price_365d_ago, chat_id FROM subs").fetchall()

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
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    with sql_lock:
        init = c.execute("SELECT ticker FROM subs WHERE chat_id=?", (chat_id,)).fetchall()
    if not init:
        init_defaults(chat_id)

    menu = [
        [InlineKeyboardButton("📌 My ETFs", callback_data="menu:list")],
        [InlineKeyboardButton("➕ Add ETF", callback_data="menu:add")],
        [InlineKeyboardButton("🔁 Force check all", callback_data="menu:check")],
        [InlineKeyboardButton("❓ Help", callback_data="menu:help")]
    ]
    await bot.send_message(chat_id, "🤖 *ETF Bot Menu:* 👇", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(menu))

async def list_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    with sql_lock:
        rows = c.execute("SELECT ticker, threshold, rebound_enabled FROM subs WHERE chat_id=?", (chat_id,)).fetchall()
    if not rows:
        return await bot.send_message(chat_id, "❗ Немає підписок", parse_mode="Markdown")

    kb = []
    for t, th, rb in rows:
        kb.append([
            InlineKeyboardButton("📊 Status", callback_data=f"status:{t}"),
            InlineKeyboardButton(f"📉 Threshold {th}%", callback_data=f"threshold_choose:{t}"),
            InlineKeyboardButton(f"🔁 Rebound {'ON' if rb else 'OFF'}", callback_data=f"rebound:{t}"),
            InlineKeyboardButton("🗑 Remove", callback_data=f"remove:{t}")
        ])
    await bot.send_message(chat_id, "📌 *Мої ETF:* 👇", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))

async def status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if not context.args:
        return await bot.send_message(chat_id, "❗ Формат: /status <ticker>")
    t = context.args[0].upper()
    text, ath = build_status_text(t, chat_id)
    if not text:
        return await bot.send_message(chat_id, "❗ Немає даних", parse_mode="Markdown")
    chart = build_chart_bytes(t, ath)
    if chart:
        await bot.send_photo(chat_id, chart, caption=text, parse_mode="Markdown")
    else:
        await bot.send_message(chat_id, text, parse_mode="Markdown")

async def add_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if not context.args:
        return await bot.send_message(chat_id, "❗ Формат: /add <ticker>")
    t = context.args[0].upper()
    now = get_price_now(t)
    ath, ath_date = get_ath_52w(t)
    if now is None or ath is None:
        return await bot.send_message(chat_id, "❗ Невалідний тікер або немає даних")
    ago = get_price_365d_ago(t) or now
    msg = f"✔ *{t} додано*\n💰 `{now:.2f} USD`\n📆 ATH `{ath:.2f} ({ath_date})`"
    chart = build_chart_bytes(t, ath)
    if chart:
        await bot.send_photo(chat_id, chart, caption=msg, parse_mode="Markdown")
    else:
        await bot.send_message(chat_id, msg, parse_mode="Markdown")
    with sql_lock:
        c.execute("INSERT OR IGNORE INTO subs(ticker,chat_id,threshold,rebound_enabled,last_alerted,rebound_sent,price_365d_ago) VALUES(?,?,?,?,?,?,?)", (t, chat_id, 5.0, 1, 0, 0, ago))
    db.commit()

async def remove_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if not context.args:
        return await bot.send_message(chat_id, "❗ Формат: /remove <ticker>")
    t = context.args[0].upper()
    with sql_lock:
        c.execute("DELETE FROM subs WHERE ticker=? AND chat_id=?", (t, chat_id))
    db.commit()
    await bot.send_message(chat_id, f"🗑 {t} removed ✔", parse_mode="Markdown")

async def rebound_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if len(context.args) < 2:
        return await bot.send_message(chat_id, "❗ Формат: /rebound <ticker> ON/OFF")
    t, st = context.args[0].upper(), context.args[1].upper()
    new = 1 if st == "ON" else 0
    with sql_lock:
        c.execute("UPDATE subs SET rebound_enabled=?, rebound_sent=0 WHERE ticker=? AND chat_id=?", (new, t, chat_id))
    db.commit()
    await bot.send_message(chat_id, f"🔁 *Rebound {t}: {st}* ✔", parse_mode="Markdown")

async def threshold_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if len(context.args) < 2:
        return await bot.send_message(chat_id, "❗ Формат: /threshold <ticker> <value>")
    t, v = context.args[0].upper(), float(context.args[1])
    with sql_lock:
        c.execute("UPDATE subs SET threshold=?, last_alerted=0, rebound_sent=0 WHERE ticker=? AND chat_id=?", (v, t, chat_id))
    db.commit()
    await bot.send_message(chat_id, f"✔ *Threshold {t} = {v}%* ✔", parse_mode="Markdown")

async def commands_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    text = (
        "ℹ️ *Команди:*\n"
        "/start\n"
        "/list\n"
        "/status <ticker>\n"
        "/add <ticker>\n"
        "/remove <ticker>\n"
        "/threshold <ticker> <value>\n"
        "/rebound <ticker> ON/OFF\n"
        "/check\n"
        "/help\n"
    )
    await bot.send_message(chat_id, text, parse_mode="Markdown")

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    text = "📘 *Help:* use /list to manage ETFs, /status <ticker> for details, /add <ticker> to subscribe."
    await bot.send_message(chat_id, text, parse_mode="Markdown")

async def check_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await status_cmd(update, context)

def inline_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # not implemented (buttons handled via patterns)
    pass

# ==== RUN ====
def main():
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("list", list_cmd))
    app.add_handler(CommandHandler("status", status_cmd))
    app.add_handler(CommandHandler("add", add_cmd))
    app.add_handler(CommandHandler("remove", remove_cmd))
    app.add_handler(CommandHandler("threshold", threshold_cmd))
    app.add_handler(CommandHandler("rebound", rebound_cmd))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("commands", commands_cmd))
    app.add_handler(CommandHandler("check", check_cmd))
    app.add_handler(CallbackQueryHandler(list_cmd, pattern="^menu:list"))
    app.add_handler(CallbackQueryHandler(help_cmd, pattern="^menu:help"))
    app.add_handler(CallbackQueryHandler(check_cmd, pattern="^menu:check"))
    app.add_handler(CallbackQueryHandler(add_cmd, pattern="^menu:add"))
    app.add_handler(CallbackQueryHandler(remove_cmd, pattern="^remove:"))
    app.add_handler(CallbackQueryHandler(rebound_cmd, pattern="^rebound:"))
    app.add_handler(CallbackQueryHandler(threshold_cmd, pattern="^threshold_set:"))
    app.add_handler(CallbackQueryHandler(threshold_cmd, pattern="^threshold_choose:"))
    threading.Thread(target=monitor_loop_runner, daemon=True).start()
    print("Bot running…")
    app.run_polling()

if __name__ == "__main__":
    main()
