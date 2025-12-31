import yfinance as yf
import sqlite3
import time
import threading
import io
import matplotlib.pyplot as plt

from telegram import Update, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters, CallbackQueryHandler

TOKEN = "8404794616:AAGNkrwRfVO9Nib0UxzvuYTJ2MElpItrkcQ"  # <-- заміни на новий токен
CHAT_ID = 409544912
INTERVAL = 600  # 10 хв

# --- Database init ---
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
def fetch_top(t):
    df = yf.Ticker(t).history(period="365d")
    if df.empty:
        return None, None
    return float(df.Close.max()), df.Close.idxmax().strftime("%Y-%m-%d")

def fetch_price(t):
    df = yf.Ticker(t).history(period="1d")
    return float(df.Close.iloc[-1]) if not df.empty else None

def fetch_ago(t):
    df = yf.Ticker(t).history(period="365d")
    return float(df.Close.iloc[0]) if not df.empty else None

def make_chart(t, top):
    df = yf.Ticker(t).history(period="365d")
    hist = df.Close
    if hist.empty:
        return None
    plt.figure()
    plt.plot(hist)
    plt.axhline(top)
    plt.title(f"{t} | TOP 365: {top:.2f}")
    buf = io.BytesIO()
    plt.savefig(buf, format="png")
    plt.close()
    buf.seek(0)
    return buf

def calc_change(now, ago):
    return (now - ago) / ago * 100 if ago else None

# --- Monitoring loop ---
def monitor():
    while True:
        rows = c.execute("SELECT ticker, threshold, rebound, last_alert, rebound_sent, top, top_date FROM subs WHERE chat_id=?", (CHAT_ID,)).fetchall()
        for t, th, rb, last, rbs, top, top_date in rows:
            now = fetch_price = fetch_price(t)
            ago = fetch_ago = fetch_ago(t)

            if now is None:
                continue

            if top == 0:
                new_top, new_date = fetch_top(t)
                if new_top:
                    c.execute("UPDATE subs SET top=?, top_date=? WHERE ticker=? AND chat_id=?", (new_top, new_date, t, CHAT_ID))
                    conn.commit()
                    top, top_date = new_top, new_date
                else:
                    continue

            dd = (top - now) / top * 100
            change = calc_change(now, ago)
            change_str = f"{change:.2f}%" if change is not None else "N/A"

            msg = (
                f"{t.upper()}\n"
                f"Ціна зараз: {now:.2f} USD\n"
                f"Зміна за 365 днів: {change_str}\n"
                f"Просадка від TOP 365: {dd:.2f}%\n"
                f"TOP 365: {top:.2f} USD ({top_date})\n"
                f"Поріг alert: {th}% | Rebound: {'ON' if rb else 'OFF'}"
            )

            # падіння алерт
            if dd >= th and last == 0:
                chart = make_chart(t, top)
                if chart:
                    bot.send_photo(chat_id=CHAT_ID, photo=chart, caption="⚠️ Падіння!\n" + msg, parse_mode="Markdown")
                else:
                    bot.send_message(chat_id=CHAT_ID, text="⚠️ Падіння!\n" + msg, parse_mode="Markdown")
                c.execute("UPDATE subs SET last_alert=1, rebound_sent=0 WHERE ticker=? AND chat_id=?", (t, CHAT_ID))
                conn.commit()

            # rebound алерт
            if dd < th and rb == 1 and last == 1 and rbs == 0:
                bot.send_message(chat_id=CHAT_ID, text="📈 Відновлення!\n" + msg, parse_mode="Markdown")
                c.execute("UPDATE subs SET rebound_sent=1 WHERE ticker=? AND chat_id=?", (t, CHAT_ID))
                conn.commit()

            # reset flags
            if dd < th and last == 1:
                c.execute("UPDATE subs SET last_alert=0 WHERE ticker=? AND chat_id=?", (t, CHAT_ID))
                conn.commit()

        time.sleep(INTERVAL)

# --- Bot handlers ---
async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    menu = ReplyKeyboardMarkup([
        ["➕ Add ETF", "📌 My ETFs"],
        ["📉 Set Threshold", "📈 Toggle Rebound"],
        ["🔁 Force Check All", "📊 Status"],
        ["❓ Help"]
    ], resize_keyboard=True)
    await update.message.reply_text("Вітаю! Обирайте команду з меню 👇", reply_markup=menu)

async def commands_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📌 Доступні команди:\n"
        "/start — меню\n"
        "/list — список ETF\n"
        "/status — статус ETF\n"
        "/commands — список команд\n"
        "/help — допомога"
    )

async def help_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "ℹ Бот моніторить ETF від річного максимуму (365d TOP).\n"
        "Алерт спрацьовує при просадці ≥ встановленого порогу.\n"
        "Є Rebound ON/OFF для сповіщення про відновлення.\n\n"
        "Тікери прикладу: SPY, QQQ, TLT"
    )

async def list_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    rows = c.execute("SELECT ticker, threshold FROM subs WHERE chat_id=?", (CHAT_ID,)).fetchall()
    if not rows:
        return await update.message.reply_text("📭 Немає підписок")
    lines = [f"{t.upper()} → поріг {th}%" for t, th in rows]
    await update.message.reply_text("📌 Ваші ETF:\n\n" + "\n".join(lines))

async def add_btn(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("✍ Введіть тікер ETF для додавання (наприклад: SPY):")
    ctx.user_data["mode"] = "add"

async def threshold_btn(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["mode"] = "threshold"
    rows = c.execute("SELECT ticker FROM subs WHERE chat_id=?", (CHAT_ID,)).fetchall()
    if not rows:
        return await update.message.reply_text("📭 Немає ETF, додай спочатку")
    tickers = [r[0] for r in rows]
    btns = [[KeyboardButton(t.upper())] for t in tickers]
    await update.message.reply_text("Оберіть ETF і введіть поріг %:", reply_markup=ReplyKeyboardMarkup(btns, resize_keyboard=True))

async def rebound_toggle_btn(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    rows = c.execute("SELECT ticker, rebound FROM subs WHERE chat_id=?", (CHAT_ID,)).fetchall()
    for t, rb in rows:
        new = 0 if rb == 1 else 1
        c.execute("UPDATE subs SET rebound=? WHERE ticker=? AND chat_id=?", (new, t, CHAT_ID))
    conn.commit()
    await update.message.reply_text("🔁 Rebound ON/OFF оновлено")

async def status_btn(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    rows = c.execute("SELECT ticker, top FROM subs WHERE chat_id=?", (CHAT_ID,)).fetchall()
    for t, _ in rows:
        top, _ = fetch_top(t)
        if top:
            chart = make_chart(t, top)
            if chart:
                bot.send_photo(chat_id=CHAT_ID, photo=chart)
    await update.message.reply_text("📊 Status оновлено")

async def text_router(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    mode = ctx.user_data.get("mode")
    text = update.message.text.strip().upper()

    if mode == "add":
        top, date = fetch_top(text)
        if top:
            c.execute("INSERT OR IGNORE INTO subs(ticker, chat_id, threshold, rebound, top, top_date) VALUES(?,?,?,?,?,?)", (text, CHAT_ID, 5, 1, top, date))
            conn.commit()
            await update.message.reply_text(f"✅ Додано {text}")
        else:
            await update.message.reply_text("❗ Невірний тікер або немає даних")
        ctx.user_data["mode"] = None
        return

    if mode == "threshold":
        c.execute("SELECT ticker FROM subs WHERE ticker=? AND chat_id=?", (text, CHAT_ID))
        if c.fetchone():
            ctx.user_data["ticker"] = text
            await update.message.reply_text("✍ Тепер введіть поріг %:")
            ctx.user_data["mode"] = "threshold_value"
        else:
            await update.message.reply_text("❗ Такого ETF немає")
        return

    if mode == "threshold_value":
        ticker = ctx.user_data.get("ticker")
        try:
            val = float(text)
            c.execute("UPDATE subs SET threshold=?, rebound=1, rebound_sent=0 WHERE ticker=? AND chat_id=?", (val, ticker, CHAT_ID))
            conn.commit()
            await update.message.reply_text(f"🔧 Поріг для {ticker} = {val}%")
        except:
            await update.message.reply_text("❗ Введіть число")
        ctx.user_data["mode"] = None
        return

    await update.message.reply_text("❗ Невідома команда. /help")

# --- Run bot ---
app = Application.builder().token(TOKEN).build()
app.add_handler(CommandHandler("start", start))
app.add_handler(CommandHandler("commands", commands_cmd))
app.add_handler(CommandHandler("list", list_cmd))
app.add_handler(CommandHandler("help", help_cmd))
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_router))

threading.Thread(target=monitor, daemon=True).start()
print("Bot running…")
app.run_polling()
