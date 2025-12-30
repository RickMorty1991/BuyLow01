import yfinance as yf
import sqlite3
import time
import threading
import io
import matplotlib.pyplot as plt

from telegram import Bot, Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import Application, CommandHandler, ContextTypes, CallbackQueryHandler, MessageHandler, filters

TELEGRAM_TOKEN = "8404794616:AAGie7vnG3LYda_QZav8KI4rxLr8XhXlAaU"  # <-- заміни на новий токен
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
            msg = f"{t}: {price_now:.2f} USD\n{yearly}\nПросадка від ATH 1Y: {dd:.2f}%"

            # падіння
            if dd >= threshold and last_alerted == 0:
                chart = build_chart_bytes(t, ath)
                if chart:
                    bot.send_photo(chat_id=CHAT_ID, photo=chart, caption="⚠️ Падіння!\n" + msg)
                else:
                    bot.send_message(chat_id=CHAT_ID, text="⚠️ Падіння!\n" + msg)

                c.execute("UPDATE subs SET last_alerted=1, rebound_sent=0 WHERE ticker=? AND chat_id=?", (t, CHAT_ID))
                conn.commit()

            # rebound 1 раз
            if dd < threshold and rebound_enabled == 1 and last_alerted == 1 and rebound_sent == 0:
                bot.send_message(chat_id=CHAT_ID, text="📈 Відновлення!\n" + msg)
                c.execute("UPDATE subs SET rebound_sent=1 WHERE ticker=? AND chat_id=?", (t, CHAT_ID))
                conn.commit()

            # reset rebound flag якщо знову пробив поріг
            if dd >= threshold and rebound_sent == 1:
                c.execute("UPDATE subs SET rebound_sent=0 WHERE ticker=? AND chat_id=?", (t, CHAT_ID))
                conn.commit()

        time.sleep(CHECK_INTERVAL)

# --- Reply menu ---
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [KeyboardButton("➕ Add ETF"), KeyboardButton("📌 My ETFs")],
        [KeyboardButton("📉 Set Threshold"), KeyboardButton("📈 Toggle Rebound")],
        [KeyboardButton("🔁 Force Check All"), KeyboardButton("📊 Status")],
        [KeyboardButton("❓ Help")]
    ]
    menu = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    await update.message.reply_text("Вітаю! Використовуйте меню 👇", reply_markup=menu)

async def commands_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = (
        "📌 *Список команд:*\n\n"
        "/start — меню\n"
        "/list — підписки ETF\n"
        "/status — стан ETF\n"
        "/commands — команди\n"
        "/help — допомога\n"
        "Також усі дії є в кнопках меню."
    )
    await update.message.reply_text(msg, parse_mode="Markdown")

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = (
        "🤖 *ETF Monitor Bot — опис опцій:*\n\n"
        "➕ *Add ETF* — додати ETF у моніторинг.\n"
        "📌 *My ETFs* — переглянути список підписок.\n"
        "📉 *Set Threshold* — встановити поріг просадки від ATH 1Y кнопками 1/3/5/7/10%.\n"
        "📈 *Toggle Rebound* — увімкнути/вимкнути алерти відновлення для кожного ETF окремо.\n"
        "🔁 *Force Check All* — перевірити всі ETF негайно.\n"
        "📊 *Status* — ціна зараз, % зміна vs 365 днів тому, дата ATH 1Y, DD від ATH.\n"
    )
    if update.message:
        await update.message.reply_text(msg, parse_mode="Markdown")
    else:
        await update.callback_query.message.reply_text(msg, parse_mode="Markdown")

async def list_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    c.execute("SELECT ticker, threshold FROM subs WHERE chat_id=?", (CHAT_ID,))
    items = c.fetchall()
    lines = [f"{t} (поріг {th}%)" for t, th in items]
    await update.message.reply_text("📌 Підписки:\n" + ("\n".join(lines) if lines else "немає"))

async def status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    c.execute("SELECT ticker FROM subs WHERE chat_id=?", (CHAT_ID,))
    items = [r[0] for r in c.fetchall()]

    lines = []
    for t in items:
        price_now = get_price(t)
        price_ago = get_price_1y_ago(t)
        ath, ath_date = get_ath_1y(t)

        if price_now and ath:
            dd = (ath - price_now) / ath * 100
            change = calc_change_percent(price_now, price_ago)
            yearly = f"Δ 1Y: {change:.2f}%" if change else "Δ 1Y: N/A"
            lines.append(f"{t}: {price_now:.2f} USD | {yearly} | DD {dd:.2f}% | ATH 1Y ({ath_date})")

    msg = "📊 *Status:*\n\n" + ("\n".join(lines) if lines else "немає даних")
    await update.message.reply_text(msg, parse_mode="Markdown")

async def threshold_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tickers = (await make_reply_list(update, "threshold_pick"))
    if not tickers:
        return

async def toggle_rebound_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tickers = (await make_reply_list(update, "toggle_rebound"))
    if not tickers:
        return

async def remove_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tickers = (await make_reply_list(update, "remove_select"))
    if not tickers:
        return

async def force_check_all(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await status_cmd(update, context)

# Будуємо кнопковий список ETF для вибору
async def make_reply_list(update, prefix):
    c.execute("SELECT ticker FROM subs WHERE chat_id=?", (CHAT_ID,))
    items = [r[0] for r in c.fetchall()]
    if not items:
        await update.message.reply_text("Немає ETF у підписках")
        return None
    buttons = [[InlineKeyboardButton(t, callback_data=f"{prefix}:{t}")] for t in items]
    await update.message.reply_text("Оберіть ETF:", reply_markup=InlineKeyboardMarkup(buttons))
    return items

# Кнопки 1/3/5/7/10% для threshold
async def threshold_pick_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    _, ticker = q.data.split(":")
    ticker = ticker.upper()

    buttons = [
        [InlineKeyboardButton("1%", callback_data=f"threshold_set:{ticker}:1")],
        [InlineKeyboardButton("3%", callback_data=f"threshold_set:{ticker}:3")],
        [InlineKeyboardButton("5%", callback_data=f"threshold_set:{ticker}:5")],
        [InlineKeyboardButton("7%", callback_data=f"threshold_set:{ticker}:7")],
        [InlineKeyboardButton("10%", callback_data=f"threshold_set:{ticker}:10")],
    ]
    await q.message.reply_text("Встановіть поріг просадки:", reply_markup=InlineKeyboardMarkup(buttons))

# threshold set
async def threshold_set_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    _, ticker, val = q.data.replace("threshold_set:", "").split(":")
    val = float(val)
    c.execute("UPDATE subs SET threshold=? WHERE ticker=? AND chat_id=?", (val, ticker, CHAT_ID))
    conn.commit()
    await q.message.reply_text(f"Поріг для {ticker} = {val}%")

# rebound toggle
async def toggle_rebound_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    _, ticker = q.data.split(":")
    ticker = ticker.upper()
    c.execute("SELECT rebound_enabled FROM subs WHERE ticker=? AND chat_id=?", (ticker, CHAT_ID))
    row = c.fetchone()
    if row:
        new_state = 0 if row[0] == 1 else 1
        c.execute("UPDATE subs SET rebound_enabled=? WHERE ticker=? AND chat_id=?", (new_state, ticker, CHAT_ID))
        conn.commit()
        await q.message.reply_text(f"Rebound для {ticker}: {'ON' if new_state else 'OFF'}")

# Add ETF через текст
async def add_text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ticker = update.message.text.strip().upper()
    c.execute("INSERT OR IGNORE INTO subs(ticker, chat_id, threshold, rebound_enabled) VALUES(?,?,5,1)", (ticker, CHAT_ID))
    conn.commit()
    await update.message.reply_text(f"Додано {ticker}")

# --- Register App ---
app = Application.builder().token(TELEGRAM_TOKEN).build()

app.add_handler(CommandHandler("start", start_cmd))
app.add_handler(CommandHandler("list", list_cmd))
app.add_handler(CommandHandler("status", status_cmd))
app.add_handler(CommandHandler("commands", commands_cmd))
app.add_handler(CommandHandler("help", help_cmd))

app.add_handler(CallbackQueryHandler(threshold_pick_handler, pattern="^threshold_pick:"))
app.add_handler(CallbackQueryHandler(toggle_rebound_handler, pattern="^toggle_rebound:"))
app.add_handler(CallbackQueryHandler(remove_cmd, pattern="^remove_select:"))
app.add_handler(CallbackQueryHandler(force_check_all, pattern="^force_check_all$"))

app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, add_text_handler))

threading.Thread(target=monitor_loop, daemon=True).start()
print("Bot running…")
app.run_polling()
