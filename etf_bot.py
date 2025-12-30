import yfinance as yf
import sqlite3
import time
import threading
import io
import matplotlib.pyplot as plt

from telegram import Bot, Update, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

TELEGRAM_TOKEN = "8404794616:AAGNkrwRfVO9Nib0UxzvuYTJ2MElpItrkcQ"  # <-- встав новий токен
CHAT_ID = 409544912
CHECK_INTERVAL = 600  # 10 хв

# --- DB ---
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

# --- Monitor thread ---
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
            yearly = f"Ціна змінилась за рік: {change:.2f}%" if change is not None else "Ціна рік тому: N/A"

            msg = f"{t}: {price_now:.2f} USD\n{yearly}\nПросадка від ATH 1Y: {dd:.2f}%"

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

# --- Меню кнопок (ReplyKeyboard) ---
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        ["➕ Add ETF", "📌 My ETFs"],
        ["📉 Set Threshold", "📈 Toggle Rebound"],
        ["🔁 Force Check All", "📊 Status"],
        ["❓ Help"]
    ]
    menu = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    await update.message.reply_text("Вітаю! Використовуйте меню 👇", reply_markup=menu)

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = (
        "📌 Опис команд:\n\n"
        "➕ Add ETF — додати ETF у моніторинг\n"
        "📌 My ETFs — список підписок\n"
        "📉 Set Threshold — встановити поріг просадки від ATH 1Y\n"
        "📈 Toggle Rebound — ON/OFF алерти відновлення окремо по ETF\n"
        "🔁 Force Check All — перевірити всі ETF негайно\n"
        "📊 Status — ціна зараз, % зміна за 365 днів, DD від ATH 1Y\n"
        "❓ Help — опис меню"
    )
    await update.message.reply_text(msg)

async def list_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    c.execute("SELECT ticker, threshold FROM subs WHERE chat_id=?", (CHAT_ID,))
    items = c.fetchall()
    if not items:
        return await update.message.reply_text("Немає підписок")
    msg = "Ваші ETF:\n" + "\n".join([f"{t} (поріг {th}%)" for t, th in items])
    await update.message.reply_text(msg)

async def status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    c.execute("SELECT ticker, threshold, rebound_enabled FROM subs WHERE chat_id=?", (CHAT_ID,))
    items = c.fetchall()
    if not items:
        return await update.message.reply_text("Немає даних")

    lines=[]
    charts=[]
    for t, th, rb in items:
        price_now = get_price(t)
        price_ago = get_price_1y_ago(t)
        ath, ath_date = get_ath_1y(t)
        if price_now and ath:
            dd = (ath - price_now) / ath * 100
            change = calc_change_percent(price_now, price_ago)
            yearly = f"{change:.2f}%" if change is not None else "N/A"
            lines.append(f"{t}: {price_now:.2f} USD | Δ1Y {yearly} | DD {dd:.2f}% | Rebound {'ON' if rb else 'OFF'} | поріг {th}%")
            chart = build_chart_bytes(t, ath)
            if chart:
                charts.append(chart)

    for chart in charts:
        bot.send_photo(chat_id=CHAT_ID, photo=chart)

    msg="📊 Status:\n\n" + "\n".join(lines)
    await update.message.reply_text(msg)

async def add_text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["action"] = "add_input"
    await update.message.reply_text("Введіть тікер ETF (приклад: SPY, QQQ):")

async def set_threshold_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["action"] = "threshold_input"
    await update.message.reply_text("Введіть поріг % просадки для сигналу (приклад: 4 або 7):")

async def toggle_rebound_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    c.execute("SELECT ticker, rebound_enabled FROM subs WHERE chat_id=?", (CHAT_ID,))
    items = c.fetchall()
    if not items:
        return await update.message.reply_text("Немає ETF")
    for t, rb in items:
        new_state = 0 if rb == 1 else 1
        c.execute("UPDATE subs SET rebound_enabled=? WHERE ticker=? AND chat_id=?", (new_state, t, CHAT_ID))
    conn.commit()
    await update.message.reply_text("🔁 Rebound стан оновлено для всіх ETF")

async def force_check_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🔁 Перевіряю всі ETF зараз...")
    await status_cmd(update, context)

# --- Роутер для Reply кнопок ---
async def reply_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text

    if text == "➕ Add ETF":
        return await add_text_handler(update, context)
    if text == "📌 My ETFs":
        return await list_cmd(update, context)
    if text == "📉 Set Threshold":
        return await set_threshold_handler(update, context)
    if text == "📈 Toggle Rebound":
        return await toggle_rebound_handler(update, context)
    if text == "🔁 Force Check All":
        return await force_check_handler(update, context)
    if text == "📊 Status":
        return await status_cmd(update, context)
    if text == "❓ Help":
        return await help_cmd(update, context)

    # Обробка введення після натискання кнопок
    action = context.user_data.get("action")
    ticker = text.upper()

    if action == "add_input":
        c.execute("INSERT OR IGNORE INTO subs(ticker, chat_id, threshold, rebound_enabled) VALUES(?,?,5,1)", (ticker, CHAT_ID))
        conn.commit()
        await update.message.reply_text(f"✅ Підписано на {ticker}")
        context.user_data["action"]=None
        return

    if action == "threshold_input":
        try:
            val=float(text)
            c.execute("UPDATE subs SET threshold=? WHERE ticker=? AND chat_id=?", (val, context.user_data.get("ticker"), CHAT_ID))
            conn.commit()
            await update.message.reply_text(f"🔧 Поріг оновлено")
        except:
            await update.message.reply_text("❗ Введіть число")
        context.user_data["action"]=None
        return

# --- Register App ---
app = Application.builder().token(TELEGRAM_TOKEN).build()

app.add_handler(CommandHandler("start", start_cmd))
app.add_handler(CommandHandler("help", help_cmd))
app.add_handler(CommandHandler("list", list_cmd))
app.add_handler(CommandHandler("commands", commands_cmd))

app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, reply_router))

threading.Thread(target=monitor_loop, daemon=True).start()
print("Bot running…")
app.run_polling()
