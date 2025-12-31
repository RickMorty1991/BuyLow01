import yfinance as yf
import sqlite3
import time
import threading
import io
import matplotlib.pyplot as plt

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters, CallbackQueryHandler

TOKEN = "8404794616:AAHUJeJp_wvOa8poUXcZufJRXXC72pZZgU0"  # ⚠ Заміни на свій токен якщо оновив
INTERVAL = 600  # 10 хв між перевірками

# --- Database setup ---
conn = sqlite3.connect("etf_top.db", check_same_thread=False)
c = conn.cursor()

c.execute("""
CREATE TABLE IF NOT EXISTS subs(
    ticker TEXT,
    chat_id INTEGER,
    threshold REAL DEFAULT 5,
    rebound INTEGER DEFAULT 1,
    last_alert INTEGER DEFAULT 0,
    rebound_sent INTEGER DEFAULT 0,
    top REAL DEFAULT 0,
    top_date TEXT DEFAULT '',
    PRIMARY KEY (ticker, chat_id)
)
""")
conn.commit()

# --- Market helpers ---
def get_top_365(ticker):
    df = yf.Ticker(ticker).history(period="365d")
    if df.empty:
        return None, None
    top = float(df.Close.max())
    top_date = df.Close.idxmax().strftime("%Y-%m-%d")
    return top, top_date

def get_price(ticker):
    df = yf.Ticker(ticker).history(period="1d")
    if df.empty:
        return None
    return float(df.Close.iloc[-1])

def get_price_1y_ago(ticker):
    df = yf.Ticker(ticker).history(period="365d")
    if df.empty:
        return None
    return float(df.Close.iloc[0])

def calc_yearly_change(now, ago):
    if now is None or ago is None or ago == 0:
        return None
    return (now - ago) / ago * 100

def build_chart(ticker, top):
    df = yf.Ticker(ticker).history(period="365d")
    hist = df.Close
    if hist.empty:
        return None

    plt.figure()
    plt.plot(hist)
    plt.axhline(top)
    plt.title(f"{ticker.upper()} | TOP 365d: {top:.2f} USD")
    plt.xlabel("Date")
    plt.ylabel("Price")

    buf = io.BytesIO()
    plt.savefig(buf, format="png")
    plt.close()
    buf.seek(0)
    return buf

# --- Monitoring loop ---
def monitor_loop(bot):
    while True:
        rows = c.execute("SELECT ticker, threshold, rebound, last_alert, rebound_sent, top, top_date, chat_id FROM subs").fetchall()

        for t, th, rb, last, rbs, top, top_date, chat_id in rows:
            now = get_price(t)
            ago = get_price_1y_ago(t)

            if now is None:
                continue

            # Якщо топ не збережено — оновлюємо
            if top == 0:
                new_top, new_date = get_top_365(t)
                if new_top:
                    c.execute("UPDATE subs SET top=?, top_date=?, rebound_sent=0 WHERE ticker=? AND chat_id=?", (new_top, new_date, t, chat_id))
                    conn.commit()
                    top, top_date = new_top, new_date
                else:
                    continue

            # Рахуємо просадку від TOP 365d
            dd = (top - now) / top * 100

            # Рахуємо зміну за рік
            yc = calc_yearly_change(now, ago)
            yc_str = f"{yc:.2f}%" if yc is not None else "N/A"

            msg = (
                f"📉 *Моніторинг {t.upper()}*\n"
                f"Ціна зараз: `{now:.2f} USD`\n"
                f"Зміна за 365 днів: `{yc_str}`\n"
                f"Просадка від TOP 365d: `{dd:.2f}%`\n"
                f"TOP 365d: `{top:.2f} USD` ({top_date})\n"
                f"Поріг alert: `{th}%` | Rebound: `{'ON' if rb else 'OFF'}'"
            )

            # 📩 Алерт про падіння
            if dd >= th and last == 0:
                chart = build_chart(t, top)
                try:
                    if chart:
                        bot.send_photo(chat_id=chat_id, photo=chart, caption="⚠️ *ETF просів нижче порогу!*\n\n" + msg, parse_mode="Markdown")
                    else:
                        bot.send_message(chat_id=chat_id, text="⚠️ *ETF просів нижче порогу!*\n\n" + msg, parse_mode="Markdown")
                except Exception as e:
                    print("Send error:", e)

                c.execute("UPDATE subs SET last_alert=1, rebound_sent=0 WHERE ticker=? AND chat_id=?", (t, chat_id))
                conn.commit()

            # 🔔 Алерт про відновлення
            if dd < th and rb == 1 and last == 1 and rbs == 0:
                try:
                    bot.send_message(chat_id=chat_id, text="📈 *Ціна відновилась (Rebound)!*\n\n" + msg, parse_mode="Markdown")
                except Exception as e:
                    print("Send error:", e)

                c.execute("UPDATE subs SET rebound_sent=1 WHERE ticker=? AND chat_id=?", (t, chat_id))
                conn.commit()

            # Скидаємо прапор падіння
            if dd < th and last == 1:
                c.execute("UPDATE subs SET last_alert=0 WHERE ticker=? AND chat_id=?", (t, chat_id))
                conn.commit()

        time.sleep(INTERVAL)

# --- Handlers ---
async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    menu = ReplyKeyboardMarkup([
        ["➕ Add ETF", "📌 My ETFs"],
        ["📉 Set Threshold", "📈 Toggle Rebound"],
        ["🔁 Force Check All", "📊 Status"],
        ["❓ Help", "/commands"]
    ], resize_keyboard=True)

    await update.message.reply_text("Вітаю! Оберіть команду 👇", reply_markup=menu)

async def commands_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📌 *Усі доступні команди:*\n\n"
        "/start — меню\n"
        "/add <ticker> — додати ETF у підписку і моніторинг\n"
        "/list — список ETF у моніторингу\n"
        "/threshold <ticker> — встановити поріг просадки\n"
        "/rebound <ticker> — ON/OFF сповіщення відновлення\n"
        "/status — перевірка всіх ETF негайно + графіки\n"
        "/commands — список команд\n"
        "/help — help меню"
    , parse_mode="Markdown")

async def help_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "ℹ *Опції меню:*\n\n"
        "➕ Add ETF — додати ETF до моніторингу\n"
        "📌 My ETFs — список підписок\n"
        "📉 Set Threshold — встановити поріг просадки\n"
        "📈 Toggle Rebound — увімк/вимк rebound сповіщення\n"
        "🔁 Force Check All — примусова перевірка\n"
        "📊 Status — статус і графіки\n"
        "❓ Help — допомога"
    , parse_mode="Markdown")

async def list_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    rows = c.execute("SELECT ticker, threshold, rebound FROM subs WHERE chat_id=?", (update.message.chat_id,)).fetchall()
    if not rows:
        return await update.message.reply_text("📭 Немає ETF. Додай через /add SPY")

    msg = "📌 *Ваші ETF у моніторингу:*\n\n" + "\n".join([f"{t.upper()} → поріг {th}% | Rebound: {'ON' if rb else 'OFF'}" for t, th, rb in rows])
    await update.message.reply_text(msg, parse_mode="Markdown")

async def add_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ticker = ctx.args[0].upper() if ctx.args else None
    chat_id = update.message.chat_id

    if not ticker:
        return await update.message.reply_text("❗ Приклад: /add SPY")

    top, d = get_top_365(ticker)
    if not top:
        return await update.message.reply_text("❗ Немає даних по тікеру")

    c.execute("INSERT OR IGNORE INTO subs(ticker, chat_id, threshold, rebound, top, top_date, last_alert, rebound_sent) VALUES(?,?,?,?,?,?,0,0)", (ticker, chat_id, 5, 1, top, d))
    conn.commit()
    await update.message.reply_text(f"✅ ETF {ticker} додано!")

async def threshold_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ticker = ctx.args[0].upper() if ctx.args else None
    if not ticker:
        return await update.message.reply_text("❗ Приклад: /threshold QQQ")

    row = c.execute("SELECT ticker FROM subs WHERE ticker=? AND chat_id=?", (ticker, update.message.chat_id)).fetchone()
    if not row:
        return await update.message.reply_text("❗ Такого ETF немає, додай через /add")

    btns = [[InlineKeyboardButton(x, callback_data=f"threshold_set:{ticker}:{x.strip('%')}")] for x in ["1%","3%","5%","7%","10%"]]
    await update.message.reply_text("Встановіть поріг:", reply_markup=InlineKeyboardMarkup(btns))

async def threshold_set_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    _, ticker, val = q.data.split(":")
    val = float(val)
    c.execute("UPDATE subs SET threshold=?, rebound=1, rebound_sent=0 WHERE ticker=? AND chat_id=?", (val, ticker, CHAT_ID))
    conn.commit()
    await q.message.reply_text(f"🔧 Поріг для {ticker} = {val}%")

# --- Router for Reply Keyboard ---
async def reply_router(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip().upper()

    if text == "➕ ADD ETF":
        return await update.message.reply_text("Введи: /add SPY")
    if text == "📌 MY ETFS":
        return await list_cmd(update, ctx)
    if text == "📉 SET THRESHOLD":
        return await threshold_cmd(update, ctx)
    if text == "📈 TOGGLE REBOUND":
        ticker = ctx.args[0].upper() if ctx.args else None
        if ticker:
            row = c.execute("SELECT rebound FROM subs WHERE ticker=? AND chat_id=?", (ticker, CHAT_ID)).fetchone()
            if row:
                new = 0 if row[0] == 1 else 1
                c.execute("UPDATE subs SET rebound=?, rebound_sent=0 WHERE ticker=? AND chat_id=?", (new, ticker, CHAT_ID))
                conn.commit()
                return await update.message.reply_text(f"🔁 Rebound {ticker}: {'ON' if new else 'OFF'}")
        return await update.message.reply_text("❗ Використай: /rebound SPY")
    if text == "🔁 FORCE CHECK ALL" or text == "📊 STATUS":
        return await status_cmd(update, ctx)
    if text == "❓ HELP":
        return await help_cmd(update, ctx)

    await update.message.reply_text("❗ Невідома команда. /help")

# --- Run ---
app = Application.builder().token(TOKEN).build()
bot = app.bot

app.add_handler(CommandHandler("start", start))
app.add_handler(CommandHandler("commands", commands_cmd))
app.add_handler(CommandHandler("list", list_cmd))
app.add_handler(CommandHandler("add", add_cmd))
app.add_handler(CommandHandler("threshold", threshold_cmd))
app.add_handler(CommandHandler("rebound", rebound_toggle_btn))
app.add_handler(CallbackQueryHandler(threshold_set_handler, pattern="^threshold_set:"))
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, reply_router))

thread = threading.Thread(target=monitor_loop, args=(bot,), daemon=True)
thread.start()

print("Bot running…")
app.run_polling()
