import yfinance as yf
import sqlite3
import time
import threading
import io
import matplotlib.pyplot as plt

from telegram import Bot, Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton, CallbackQuery
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters, CallbackQueryHandler

TELEGRAM_TOKEN = "8404794616:AAGNkrwRfVO9Nib0UxzvuYTJ2MElpItrkcQ"  # <-- заміни на новий токен
CHAT_ID = 409544912
CHECK_INTERVAL = 600  # 10 хв

# --- Database ---
conn = sqlite3.connect("etf_top.db", check_same_thread=False)
c = conn.cursor()

c.execute("""CREATE TABLE IF NOT EXISTS subs(
    ticker TEXT,
    chat_id INTEGER,
    threshold REAL DEFAULT 5,
    rebound_enabled INTEGER DEFAULT 1,
    last_alerted INTEGER DEFAULT 0,
    rebound_sent INTEGER DEFAULT 0,
    price_ago REAL DEFAULT 0,
    PRIMARY KEY (ticker, chat_id)
)""")
conn.commit()

bot = Bot(token=TELEGRAM_TOKEN)

# --- Helpers ---
def get_price(ticker):
    df = yf.Ticker(ticker).history(period="1d")
    return float(df['Close'].iloc[-1]) if not df.empty else None

def get_price_1y_ago(ticker):
    df = yf.Ticker(ticker).history(period="365d")
    return float(df['Close'].iloc[0]) if not df.empty else None

def get_ath_1y(ticker):
    df = yf.Ticker(ticker).history(period="365d")
    if df.empty:
        return None, None
    return float(df['Close'].max()), df['Close'].idxmax().strftime("%Y-%m-%d")

def build_chart_bytes(ticker, ath):
    df = yf.Ticker(ticker).history(period="365d")
    hist = df['Close']
    if hist.empty:
        return None
    plt.figure()
    plt.plot(hist)
    plt.axhline(ath)
    plt.title(f"{ticker} | ATH 1Y: {ath:.2f}")
    plt.xlabel("Date")
    plt.ylabel("Price")
    buf = io.BytesIO()
    plt.savefig(buf, format="png")
    plt.close()
    buf.seek(0)
    return buf

def calc_change_percent(now, ago):
    return (now - ago) / ago * 100 if ago else None

# --- Monitoring Loop ---
def monitor_loop():
    while True:
        c.execute("SELECT ticker, threshold, rebound_enabled, last_alerted, rebound_sent FROM subs WHERE chat_id=?", (CHAT_ID,))
        items = c.fetchall()

        for t, threshold, rebound_enabled, last_alerted, rebound_sent in items:
            price_now = get_price(t)
            price_ago = get_price_1y_ago(t)
            ath, ath_date = get_ath_1y(t)

            if price_now is None or ath is None:
                continue

            dd = (ath - price_now) / ath * 100
            change = calc_change_percent(price_now, price_ago)
            yearly = f"Δ 1Y: {change:.2f}%" if change is not None else "Δ 1Y: N/A"

            msg = f"{t}: {price_now:.2f} USD\n{yearly}\nПросадка від ATH 1Y: {dd:.2f}% | ATH({ath_date})"

            if dd >= threshold and last_alerted == 0:
                chart = build_chart_bytes(t, ath)
                if chart:
                    bot.send_photo(chat_id=CHAT_ID, photo=chart, caption="⚠️ Падіння!\n" + msg)
                else:
                    bot.send_message(chat_id=CHAT_ID, text="⚠️ Падіння!\n" + msg)
                c.execute("UPDATE subs SET last_alerted=1, rebound_sent=0 WHERE ticker=? AND chat_id=?", (t, CHAT_ID))
                conn.commit()

            if dd < threshold and rebound_enabled == 1 and last_alerted == 1 and rebound_sent == 0:
                bot.send_message(chat_id=CHAT_ID, text="📈 Відновлення!\n" + msg)
                c.execute("UPDATE subs SET rebound_sent=1 WHERE ticker=? AND chat_id=?", (t, CHAT_ID))
                conn.commit()

            if dd >= threshold and rebound_sent == 1:
                c.execute("UPDATE subs SET rebound_sent=0 WHERE ticker=? AND chat_id=?", (t, CHAT_ID))
                conn.commit()

        time.sleep(CHECK_INTERVAL)

# --- Commands ---
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    menu = ReplyKeyboardMarkup([
        ["➕ Add ETF", "📌 My ETFs"],
        ["📉 Set Threshold", "📈 Toggle Rebound"],
        ["🔁 Force Check All", "📊 Status"],
        ["❓ Help"]
    ], resize_keyboard=True)
    await update.message.reply_text("Вітаю! Використовуйте меню 👇", reply_markup=menu)

async def commands_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📌 Доступні команди:\n"
        "/start — меню\n"
        "/list — ваші ETF\n"
        "/status — стан ETF\n"
        "/commands — список команд\n"
        "/help — допомога"
    )

async def list_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    c.execute("SELECT ticker, threshold FROM subs WHERE chat_id=?", (CHAT_ID,))
    items = c.fetchall()
    await update.message.reply_text("📌 Ваші ETF:\n" + ("\n".join([f"{t} (поріг {th}%)" for t, th in items]) if items else "немає"))

async def status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    c.execute("SELECT ticker FROM subs WHERE chat_id=?", (CHAT_ID,))
    items=[r[0] for r in c.fetchall()]
    lines=[]
    charts=[]
    for t in items:
        price_now=get_price(t)
        ath,ath_date=get_ath_1y(t)
        price_ago=get_price_1y_ago(t)
        if price_now and ath:
            dd=(ath-price_now)/ath*100
            change=calc_change_percent(price_now,price_ago)
            yearly=f"Δ 1Y: {change:.2f}%" if change else "Δ 1Y: N/A"
            lines.append(f"{t}: {price_now:.2f} USD | {yearly} | DD {dd:.2f}% | ATH({ath_date})")
            ch=build_chart_bytes(t,ath)
            if ch: charts.append(ch)
    for ch in charts:
        bot.send_photo(chat_id=CHAT_ID,photo=ch)
    await update.message.reply_text("📊 Status:\n\n"+("\n".join(lines) if lines else "немає даних"))

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Опис опцій у меню:\nAdd ETF | My ETFs | Set Threshold | Toggle Rebound | Force Check All | Status | Help")

# --- Routers ---
async def reply_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text=update.message.text
    if text=="📌 My ETFs" or text=="➕ Add ETF" or text=="📉 Set Threshold" or text=="📈 Toggle Rebound" or text=="🔁 Force Check All":
        return await update.message.reply_text("Оберіть команду через /start або /commands.")
    return await update.message.reply_text("Невідома команда. /help")

# --- App run ---
app = Application.builder().token(TELEGRAM_TOKEN).build()
app.add_handler(CommandHandler("start", start_cmd))
app.add_handler(CommandHandler("list", list_cmd))
app.add_handler(CommandHandler("status", status_cmd))
app.add_handler(CommandHandler("commands", commands_cmd))
app.add_handler(CommandHandler("help", help_cmd))
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, reply_router))

threading.Thread(target=monitor_loop, daemon=True).start()
print("Bot running…")
app.run_polling()

