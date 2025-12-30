import yfinance as yf
import sqlite3
import time
import matplotlib.pyplot as plt
from telegram import Bot, Update
from telegram.ext import Application, CommandHandler, ContextTypes
import threading
import os

TELEGRAM_TOKEN = "8404794616:AAHUJeJp_wvOa8poUXcZufJRXXC72pZZgU0"
CHAT_ID = 409544912
CHECK_INTERVAL = 600  # 10 хв (краще для free VPS)

# --- SQLite ---
conn = sqlite3.connect("etf_top.db", check_same_thread=False)
c = conn.cursor()

# таблиця ETF
c.execute("""CREATE TABLE IF NOT EXISTS etf(
    ticker TEXT PRIMARY KEY,
    top REAL,
    top_date TEXT,
    threshold REAL DEFAULT 5,
    alerted INTEGER DEFAULT 0,
    rebound INTEGER DEFAULT 1
)""")

# таблиця підписок користувача
c.execute("""CREATE TABLE IF NOT EXISTS subs(
    ticker TEXT,
    chat_id INTEGER,
    PRIMARY KEY (ticker, chat_id)
)""")

conn.commit()
bot = Bot(token=TELEGRAM_TOKEN)

def get_price(ticker):
    df = yf.Ticker(ticker).history(period="1d")
    return float(df['Close'].iloc[-1]) if not df.empty else None

def get_ath_year(ticker):
    df = yf.Ticker(ticker).history(period="52wk")
    if df.empty:
        return None, None
    ath = float(df['Close'].max())
    ath_date = df['Close'].idxmax().strftime("%Y-%m-%d")
    return ath, ath_date

def make_chart(ticker, ath, top_date):
    df = yf.Ticker(ticker).history(period="52wk")
    hist = df['Close']
    if hist.empty:
        return None
    plt.figure()
    plt.plot(hist)
    plt.axhline(ath)
    plt.title(f"{ticker} price 52wk | ATH 1Y: {ath:.2f} ({top_date})")
    plt.xlabel("Date")
    plt.ylabel("Price")
    filename = f"{ticker}_chart.png"
    plt.savefig(filename)
    plt.close()
    return filename

def check_etf_once():
    # які ETF моніторимо в subs для цього CHAT_ID
    c.execute("SELECT ticker FROM subs WHERE chat_id=?", (CHAT_ID,))
    etfs = [r[0] for r in c.fetchall()]

    for t in etfs:
        price = get_price(t)
        if price is None:
            continue

        ath, top_date = get_ath_year(t)

        c.execute("SELECT top, top_date, threshold, alerted, rebound FROM etf WHERE ticker=?", (t,))
        row = c.fetchone()

        if row is None:
            c.execute("INSERT INTO etf(ticker, top, top_date, threshold, alerted, rebound) VALUES(?,?,?,?,?,?)",
                      (t, ath, top_date, 5, 0, 1))
            conn.commit()
            top_db, date_db, threshold, alerted, rebound = ath, top_date, 5, 0, 1
        else:
            top_db, date_db, threshold, alerted, rebound = row

        if ath > top_db:
            top_db = ath
            date_db = top_date
            c.execute("UPDATE etf SET top=?, top_date=?, alerted=0, rebound=1 WHERE ticker=?", (top_db, date_db, t))
            conn.commit()

        dd = (top_db - price) / top_db * 100
        msg = f"{t} просів на {dd:.2f}% від ATH за рік\nЦіна: {price:.2f} USD\nATH 1Y: {top_db:.2f} ({date_db})\nПоріг: {threshold}%"

        if dd >= threshold and alerted == 0:
            file = make_chart(t, top_db, date_db)
            if file:
                bot.send_photo(chat_id=CHAT_ID, photo=open(file,"rb"), caption="⚠️ " + msg)
                os.remove(file)
            else:
                bot.send_message(chat_id=CHAT_ID, text="⚠️ " + msg)

            c.execute("UPDATE etf SET alerted=1, rebound=0 WHERE ticker=?", (t,))
            conn.commit()

        if dd < threshold and rebound == 0:
            bot.send_message(chat_id=CHAT_ID, text="📈 Відновлення: " + msg)
            c.execute("UPDATE etf SET rebound=1 WHERE ticker=?", (t,))
            conn.commit()

        if dd >= threshold and rebound == 1:
            c.execute("UPDATE etf SET rebound=0 WHERE ticker=?", (t,))
            conn.commit()

# --- Команди Telegram ---
async def add_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) != 1:
        await update.message.reply_text("Формат: /add TICKER")
        return
    t = context.args[0].upper()

    # додати у підписки
    try:
        c.execute("INSERT OR IGNORE INTO subs(ticker, chat_id) VALUES(?,?)", (t, CHAT_ID))
        conn.commit()
        await update.message.reply_text(f"✅ Додано {t} у моніторинг і підписку")
    except:
        await update.message.reply_text("Помилка додавання")

async def list_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    c.execute("SELECT ticker FROM subs WHERE chat_id=?", (CHAT_ID,))
    items = [r[0] for r in c.fetchall()]
    await update.message.reply_text("📌 Підписані ETF:\n" + ("\n".join(items) if items else "немає"))

async def threshold_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) != 2:
        await update.message.reply_text("Формат: /threshold TICKER %")
        return
    t = context.args[0].upper()
    try:
        val = float(context.args[1])
        c.execute("UPDATE etf SET threshold=?, alerted=0, rebound=1 WHERE ticker=?", (val, t))
        conn.commit()
        await update.message.reply_text(f"🔧 Поріг для {t} = {val}%")
    except:
        await update.message.reply_text("Помилка встановлення порогу")

async def remove_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) != 1:
        await update.message.reply_text("Формат: /remove TICKER")
        return
    t = context.args[0].upper()
    c.execute("DELETE FROM subs WHERE ticker=? AND chat_id=?", (t, CHAT_ID))
    conn.commit()
    await update.message.reply_text(f"🗑 Видалено {t} з підписки")

async def status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lines=[]
    c.execute("SELECT top, top_date, threshold FROM etf")
    aths={r[0]:r[1:] for r in c.fetchall()}
    c.execute("SELECT ticker FROM subs WHERE chat_id=?", (CHAT_ID,))
    for t in [r[0] for r in c.fetchall()]:
        price=get_price(t)
        ath, date = get_ath_year(t)
        if price and ath:
            dd=(ath-price)/ath*100
            lines.append(f"{t}: {price:.2f} USD | DD: {dd:.2f}% | ATH 1Y: {ath:.2f} ({date})")
    await update.message.reply_text("📊 Статус:\n"+("\n".join(lines) if lines else "немає даних"))

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Команди: /add, /list, /threshold, /remove, /status, /help")

app = Application.builder().token(TELEGRAM_TOKEN).build()
app.add_handler(CommandHandler("add", add_cmd))
app.add_handler(CommandHandler("list", list_cmd))
app.add_handler(CommandHandler("threshold", threshold_cmd))
app.add_handler(CommandHandler("remove", remove_cmd))
app.add_handler(CommandHandler("status", status_cmd))
app.add_handler(CommandHandler("help", help_cmd))

threading.Thread(target=check_etf_once, daemon=True).start()
print("Bot running…")
app.run_polling()
