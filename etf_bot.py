import yfinance as yf
import sqlite3
import time
import threading
import io
import matplotlib.pyplot as plt

from telegram import Bot, Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters, CallbackQueryHandler

TELEGRAM_TOKEN = "8404794616:AAHUJeJp_wvOa8poUXcZufJRXXC72pZZgU0"  # <-- заміни на новий токен
CHAT_ID = 409544912
CHECK_INTERVAL = 600  # 10 хв

# --- Database setup ---
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

# --- Helper functions ---
def get_price_now(ticker):
    df = yf.Ticker(ticker).history(period="1d")
    return float(df['Close'].iloc[-1]) if not df.empty else None

def get_price_1y_ago(ticker):
    df = yf.Ticker(ticker).history(period="365d")
    return float(df['Close'].iloc[0]) if not df.empty else None

def get_ath_1y(ticker):
    df = yf.Ticker(ticker).history(period="365d")
    if df.empty:
        return None, None
    ath = float(df['Close'].max())
    ath_date = df['Close'].idxmax().strftime("%Y-%m-%d")
    return ath, ath_date

def calc_change_percent(now, ago):
    return (now - ago) / ago * 100 if ago else None

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

# --- Monitoring loop ---
def monitor_loop():
    while True:
        c.execute("SELECT ticker, threshold, rebound_enabled, last_alerted, rebound_sent FROM subs WHERE chat_id=?", (CHAT_ID,))
        items = c.fetchall()

        for ticker, threshold, rebound_enabled, last_alerted, rebound_sent in items:
            price_now = get_price_now(ticker)
            price_ago = get_price_1y_ago(ticker)
            ath, ath_date = get_ath_1y(ticker)

            if price_now is None or ath is None:
                continue

            dd = (ath - price_now) / ath * 100
            change = calc_change_percent(price_now, price_ago)
            yearly = f"Δ 1Y: {change:.2f}%" if change is not None else "Δ 1Y: N/A"

            msg = (
                f"{ticker}: {price_now:.2f} USD\n"
                f"{yearly}\n"
                f"Просадка від ATH 1Y: {dd:.2f}%\n"
                f"ATH 1Y: {ath:.2f} ({ath_date})"
            )

            if dd >= threshold and last_alerted == 0:
                chart = build_chart_bytes(ticker, ath)
                if chart:
                    bot.send_photo(chat_id=CHAT_ID, photo=chart, caption="⚠️ Падіння!\n" + msg)
                else:
                    bot.send_message(chat_id=CHAT_ID, text="⚠️ Падіння!\n" + msg)
                c.execute("UPDATE subs SET last_alerted=1, rebound_sent=0 WHERE ticker=? AND chat_id=?", (ticker, CHAT_ID))
                conn.commit()

            if dd < threshold and rebound_enabled == 1 and last_alerted == 1 and rebound_sent == 0:
                bot.send_message(chat_id=CHAT_ID, text="📈 Відновлення!\n" + msg)
                c.execute("UPDATE subs SET rebound_sent=1 WHERE ticker=? AND chat_id=?", (ticker, CHAT_ID))
                conn.commit()

            if dd >= threshold and rebound_sent == 1:
                c.execute("UPDATE subs SET rebound_sent=0 WHERE ticker=? AND chat_id=?", (ticker, CHAT_ID))
                conn.commit()

        time.sleep(CHECK_INTERVAL)

# --- Command handlers ---
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
        "🤖 *ETF Monitor Bot — опис опцій меню:*\n\n"
        "➕ *Add ETF* — Додати ETF у моніторинг і підписку на алерти.\n"
        "📌 *My ETFs* — Показати список ваших підписок та пороги просадки.\n"
        "📉 *Set Threshold* — Встановити поріг просадки від ATH 1Y (вибір кнопками 1/3/5/7/10%).\n"
        "📈 *Toggle Rebound* — Увімкнути/вимкнути сповіщення про відновлення ціни для кожного ETF окремо.\n"
        "🔁 *Force Check All* — Примусово перевірити всі ETF негайно і отримати статус.\n"
        "📊 *Status* — Показує: поточну ціну, % зміну vs 365 днів тому, просадку від ATH 1Y, дату ATH.\n"
        "❓ *Help* — Пояснення меню.\n"
    )
    await update.message.reply_text(msg, parse_mode="Markdown")

async def commands_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = (
        "📌 *Доступні команди:*\n\n"
        "/start — Відкрити меню\n"
        "/add — Додати ETF\n"
        "/list — Список підписок\n"
        "/status — Перевірити стан ETF\n"
        "/commands — Список команд\n"
        "/help — Опис меню\n"
    )
    await update.message.reply_text(msg, parse_mode="Markdown")

async def list_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    c.execute("SELECT ticker, threshold FROM subs WHERE chat_id=?", (CHAT_ID,))
    items = c.fetchall()
    if not items:
        return await update.message.reply_text("Немає підписок")
    msg = "📌 *Ваші ETF:*\n\n" + "\n".join([f"{t} (поріг {th}%)" for t, th in items])
    await update.message.reply_text(msg, parse_mode="Markdown")

async def status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    c.execute("SELECT ticker FROM subs WHERE chat_id=?", (CHAT_ID,))
    items = [r[0] for r in c.fetchall()]
    if not items:
        return await update.message.reply_text("Немає даних")

    lines=[]
    charts=[]
    for t in items:
        price_now = get_price_now(t)
        ath, ath_date = get_ath_1y(t)
        price_ago = get_price_1y_ago(t)
        change = calc_change_percent(price_now, price_ago)

        if price_now and ath:
            dd = (ath - price_now) / ath * 100
            yearly = f"{change:.2f}%" if change else "N/A"
            lines.append(f"{t}: {price_now:.2f} USD | Δ1Y {yearly} | DD {dd:.2f}% | ATH ({ath_date})")
            chart = build_chart_bytes(t, ath)
            if chart:
                charts.append(chart)

    for chart in charts:
        bot.send_photo(chat_id=CHAT_ID, photo=chart)

    msg="📊 *Status:*\n\n" + "\n".join(lines)
    await update.message.reply_text(msg, parse_mode="Markdown")

async def add_input_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Введіть тікер ETF (SPY, QQQ, etc):")
    context.user_data["action"] = "add_input"

async def text_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip().upper()
    action = context.user_data.get("action")

    if text == "➕ ADD ETF":
        return await add_input_cmd(update, context)
    if text == "📌 MY ETFS":
        return await list_cmd(update, context)
    if text == "📉 SET THRESHOLD":
        return await update.message.reply_text("Використайте /threshold для вибору ETF")
    if text == "📈 TOGGLE REBOUND":
        return await update.message.reply_text("Перемикач rebound через /rebound")
    if text == "🔁 FORCE CHECK ALL":
        return await status_cmd(update, context)
    if text == "📊 STATUS":
        return await status_cmd(update, context)
    if text == "❓ HELP":
        return await help_cmd(update, context)

    if action == "add_input":
        c.execute("INSERT OR IGNORE INTO subs(ticker, chat_id, threshold, rebound_enabled) VALUES(?,?,5,1)", (text, CHAT_ID))
        conn.commit()
        await update.message.reply_text(f"✅ Додано {text}")
        context.user_data["action"] = None
        return await list_cmd(update, context)

# --- Register Application ---
app = Application.builder().token(TELEGRAM_TOKEN).build()

app.add_handler(CommandHandler("start", start_cmd))
app.add_handler(CommandHandler("help", help_cmd))
app.add_handler(CommandHandler("list", list_cmd))
app.add_handler(CommandHandler("status", status_cmd))
app.add_handler(CommandHandler("commands", commands_cmd))
app.add_handler(CommandHandler("add", add_input_cmd))

app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_router))

threading.Thread(target=monitor_loop, daemon=True).start()
print("Service is running…")
app.run_polling()
