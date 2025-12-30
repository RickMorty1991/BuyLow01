import yfinance as yf
import sqlite3
import time
import matplotlib.pyplot as plt
from telegram import Bot, Update
from telegram.ext import Application, CommandHandler, ContextTypes
import threading

TELEGRAM_TOKEN = "8404794616:AAHUJeJp_wvOa8poUXcZufJRXXC72pZZgU0"
CHAT_ID = 409544912
CHECK_INTERVAL = 900  # 15 хв

ETFS = ["SPY", "QQQ"]
THRESHOLDS = {"SPY": 4, "QQQ": 7}

# --- SQLite ---
conn = sqlite3.connect("etf_top.db", check_same_thread=False)
c = conn.cursor()
c.execute("""CREATE TABLE IF NOT EXISTS etf(
    ticker TEXT PRIMARY KEY,
    top REAL,
    top_date TEXT,
    alerted INTEGER DEFAULT 0,
    rebound INTEGER DEFAULT 0
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
    return float(df['Close'].max()), df['Close'].idxmax().strftime("%Y-%m-%d")

def get_price_history_year(ticker):
    return yf.Ticker(ticker).history(period="52wk")['Close']

def make_chart(ticker, ath, top_date):
    hist = get_price_history_year(ticker)
    if hist.empty:
        return None

    plt.figure()
    plt.plot(hist)
    plt.axhline(ath)
    plt.title(f"{ticker} price 52wk | ATH: {ath:.2f} ({top_date})")
    plt.xlabel("Date")
    plt.ylabel("Price")

    filename = f"{ticker}_chart.png"
    plt.savefig(filename)
    plt.close()
    return filename

def check_etf_once():
    for t in ETFS:
        price = get_price(t)
        if price is None:
            continue

        ath, top_date = get_ath_year(t)
        if ath is None:
            continue

        c.execute("SELECT top, top_date, alerted, rebound FROM etf WHERE ticker=?", (t,))
        row = c.fetchone()

        if row is None:
            c.execute("INSERT INTO etf(ticker, top, top_date, alerted, rebound) VALUES(?,?,?,?,?)",
                      (t, ath, top_date, 0, 1))
            conn.commit()
            top, date_db, alerted, rebound = ath, top_date, 0, 1
        else:
            top, date_db, alerted, rebound = row

        if ath > top:
            top = ath
            date_db = top_date
            c.execute("UPDATE etf SET top=?, top_date=?, alerted=0, rebound=1 WHERE ticker=?", (top, date_db, t))
            conn.commit()

        dd = (top - price) / top * 100
        threshold = THRESHOLDS[t]

        print(f"{t}: {price:.2f} | ATH 1Y: {top:.2f} ({date_db}) | DD: {dd:.2f}%")

        if dd >= threshold and alerted == 0:
            chart = make_chart(t, top, date_db)
            msg = f"⚠️ {t} впав на {dd:.2f}% від річного ATH!\nЦіна: {price:.2f} USD\nATH 1Y: {top:.2f} ({date_db})"

            if chart:
                bot.send_photo(chat_id=CHAT_ID, photo=open(chart, "rb"), caption=msg)
            else:
                bot.send_message(chat_id=CHAT_ID, text=msg)

            c.execute("UPDATE etf SET alerted=1, rebound=0 WHERE ticker=?", (t,))
            conn.commit()

        if dd < threshold and rebound == 0:
            msg = f"📈 {t} відновився!\nЦіна: {price:.2f} USD\nПадіння від ATH: {dd:.2f}%"
            bot.send_message(chat_id=CHAT_ID, text=msg)

            c.execute("UPDATE etf SET rebound=1 WHERE ticker=?", (t,))
            conn.commit()

        if dd >= threshold and rebound == 1:
            c.execute("UPDATE etf SET rebound=0 WHERE ticker=?", (t,))
            conn.commit()

def run_loop():
    while True:
        try:
            check_etf_once()
        except Exception as e:
            print("Error:", e)
        time.sleep(CHECK_INTERVAL)

async def status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lines = []
    for t in ETFS:
        price = get_price(t)
        ath, date = get_ath_year(t)
        if price and ath:
            dd = (ath - price) / ath * 100
            lines.append(f"{t}: {price:.2f} USD | ATH 1Y: {ath:.2f} ({date}) | DD: {dd:.2f}% | Alert @ {THRESHOLDS[t]}%")

    msg = "📊 *Статус ETF*\n" + "\n".join(lines)
    await update.message.reply_text(msg, parse_mode="Markdown")

async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("ETF-монітор працює 🚀\n/status — перевірити стан вручну")

app = Application.builder().token(TELEGRAM_TOKEN).build()
app.add_handler(CommandHandler("status", status_cmd))
app.add_handler(CommandHandler("start", start_cmd))

threading.Thread(target=run_loop, daemon=True).start()

print("Бот працює…")
app.run_polling()

