import yfinance as yf
import sqlite3
import time
import threading
import io
import matplotlib.pyplot as plt

from telegram import Bot, Update, ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardButton, InlineKeyboardMarkup, CallbackQuery
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters, CallbackQueryHandler

# ==== SETTINGS ====
TELEGRAM_TOKEN = "8404794616:AAH0P9fRUcr05IygMuiXCz-lrNPQCUNFlh8"  # <-- ВПИШИ СВІЙ НОВИЙ TOKEN ТУТ
CHAT_ID = 409544912
CHECK_INTERVAL = 600  # 10 хв

# ==== DATABASE ====
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

# ==== FINANCE HELPERS ====
def get_price_now(ticker):
    df = yf.Ticker(ticker).history(period="1d")
    return float(df["Close"].iloc[-1]) if not df.empty else None

def get_price_1y_ago(ticker):
    df = yf.Ticker(ticker).history(period="365d")
    return float(df["Close"].iloc[0]) if not df.empty else None

def get_ath_1y(ticker):
    df = yf.Ticker(ticker).history(period="365d")
    if df.empty:
        return None, None
    return float(df["Close"].max()), df["Close"].idxmax().strftime("%Y-%m-%d")

def calc_change_percent(now, ago):
    return (now - ago) / ago * 100 if ago else None

def build_chart_bytes(ticker, ath):
    df = yf.Ticker(ticker).history(period="365d")
    hist = df["Close"]
    if hist.empty:
        return None
    plt.figure()
    plt.plot(hist)
    plt.axhline(ath)
    plt.title(f"{ticker} | ATH 1Y {ath:.2f} USD")
    buf = io.BytesIO()
    plt.savefig(buf, format="png")
    plt.close()
    buf.seek(0)
    return buf

# ==== MONITORING LOOP ====
def monitor_loop():
    while True:
        items = c.execute("SELECT ticker, threshold, rebound_enabled, last_alerted, rebound_sent FROM subs WHERE chat_id=?", (CHAT_ID,)).fetchall()

        for ticker, threshold, rebound_enabled, last_alerted, rebound_sent in items:
            price_now = get_price_now(ticker)
            price_ago = get_price_1y_ago(ticker)
            ath, ath_date = get_ath_1y(ticker)

            if price_now is None or ath is None:
                continue

            dd = (ath - price_now) / ath * 100
            change = calc_change_percent(price_now, price_ago)

            msg = (
                f"📊 {ticker}\n"
                f"💰 Ціна зараз: {price_now:.2f} USD\n"
            )

            if change is not None:
                direction = "📈 виріс" if change > 0 else "📉 впав"
                msg += f"{direction} за 365d: {abs(change):.2f}%\n"
            else:
                msg += "Δ365d: N/A\n"

            msg += f"📉 Просадка від ATH 1Y: {dd:.2f}%\n📆 ATH 1Y: {ath:.2f} USD ({ath_date})"

            # Алерт на падіння
            if dd >= threshold and last_alerted == 0:
                chart = build_chart_bytes(ticker, ath)
                if chart:
                    app_bot.send_photo(CHAT_ID, chart, caption="⚠️ *Просадка від ATH!*\n\n" + msg, parse_mode="Markdown")
                else:
                    app_bot.send_message(CHAT_ID, "⚠️ *Просадка від ATH!*\n\n" + msg, parse_mode="Markdown")

                c.execute("UPDATE subs SET last_alerted=1, rebound_sent=0 WHERE ticker=? AND chat_id=?", (ticker, CHAT_ID))
                conn.commit()

            # Алерт на відновлення
            if dd < threshold and rebound_enabled == 1 and last_alerted == 1 and rebound_sent == 0:
                app_bot.send_message(CHAT_ID, "📈 *Відновлення ціни!*\n\n" + msg, parse_mode="Markdown")
                c.execute("UPDATE subs SET rebound_sent=1 WHERE ticker=? AND chat_id=?", (ticker, CHAT_ID))
                conn.commit()

            # Скидання rebound_sent
            if dd >= threshold and rebound_sent == 1:
                c.execute("UPDATE subs SET rebound_sent=0 WHERE ticker=? AND chat_id=?", (ticker, CHAT_ID))
                conn.commit()

        time.sleep(CHECK_INTERVAL)

# ==== BOT HANDLERS ====
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        ["➕ Add ETF", "📌 My ETFs"],
        ["📉 Set Threshold", "📈 Toggle Rebound"],
        ["🔁 Force Check All", "📊 Status"],
        ["🗑 Remove ETF", "❓ Help", "📌 Commands"]
    ]
    menu = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    await update.message.reply_text("Вітаю! Оберіть команду з меню 👇", reply_markup=menu)

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = (
        "🤖 *ETF Monitor Bot — Help Menu*\n\n"
        "➕ *Add ETF* — додати новий ETF у відстеження.\n"
        "📌 *My ETFs* — список ETF, які моніторяться, з кнопками видалення.\n"
        "📉 *Set Threshold* — встановити поріг просадки від річного ATH для обраного ETF.\n"
        "📈 *Toggle Rebound* — увімк/вимк алерти відновлення для обраного ETF.\n"
        "🔁 *Force Check All* — негайна перевірка всіх ETF.\n"
        "📊 *Status* — статус ETF (ціна зараз, % зміна за 365 днів, просадка від ATH 1Y + графік).\n"
        "🗑 *Remove ETF* — видалити ETF зі списку вручну або через меню.\n\n"
        "Доступні команди:\n"
        "/start — відкрити меню\n"
        "/list — показати список ETF\n"
        "/status — статус ETF\n"
        "/threshold — встановити поріг просадки\n"
        "/rebound — Toggle rebound ON/OFF\n"
        "/remove — видалити ETF\n"
        "/commands — список команд\n"
        "/help — допомога"
    )
    await update.message.reply_text(msg, parse_mode="Markdown")

async def commands_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = (
        "📌 *Доступні команди:*\n\n"
        "/start — меню\n"
        "/list — список ваших ETF\n"
        "/status — перевірити статус ETF\n"
        "/threshold — встановити поріг просадки для 1 ETF\n"
        "/rebound — увімк/вимк Rebound для 1 ETF\n"
        "/remove — видалити ETF\n"
        "/help — Help menu\n"
        "/commands — список команд"
    )
    await update.message.reply_text(msg, parse_mode="Markdown")

async def list_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    items = c.execute("SELECT ticker, threshold, rebound_enabled FROM subs WHERE chat_id=?", (CHAT_ID,)).fetchall()
    if not items:
        return await update.message.reply_text("❗ Немає ETF у списку")

    # Inline-кнопки видалення всередині списку
    buttons = []
    for t, th, rb in items:
        label = f"{t} (поріг {th}%) | Rebound {'ON' if rb else 'OFF'}"
        buttons.append([
            InlineKeyboardButton(label, callback_data="noop"),
            InlineKeyboardButton("🗑 Remove", callback_data=f"remove:{t}")
        ])

    await update.message.reply_text("📌 *Ваші ETF:* 👇", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(buttons))

async def threshold_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    items = [r[0] for r in c.execute("SELECT ticker FROM subs WHERE chat_id=?", (CHAT_ID,)).fetchall()]
    if not items:
        return await update.message.reply_text("❗ Немає ETF")

    btn = [[InlineKeyboardButton(t, callback_data=f"threshold:{t}")] for t in items]
    await update.message.reply_text("Оберіть ETF для встановлення порогу:", reply_markup=InlineKeyboardMarkup(btn))

async def threshold_pick(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    ticker = q.data.split(":")[1].upper()

    thb = [
        [InlineKeyboardButton("1%", callback_data=f"threshold_save:{ticker}:1")],
        [InlineKeyboardButton("3%", callback_data=f"threshold_save:{ticker}:3")],
        [InlineKeyboardButton("5%", callback_data=f"threshold_save:{ticker}:5")],
        [InlineKeyboardButton("7%", callback_data=f"threshold_save:{ticker}:7")],
        [InlineKeyboardButton("10%", callback_data=f"threshold_save:{ticker}:10")]
    ]
    await q.message.reply_text("Виберіть поріг просадки %:", reply_markup=InlineKeyboardMarkup(thb))

async def threshold_save(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    _, t, val = update.callback_query.data.split(":")
    ticker = t.upper()

    c.execute("UPDATE subs SET threshold=? WHERE ticker=? AND chat_id=?", (float(val), ticker, CHAT_ID))
    conn.commit()
    await q.message.reply_text(f"✔ Поріг для {ticker} = {val}%")

async def rebound_toggle_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    items = c.execute("SELECT ticker, rebound_enabled FROM subs WHERE chat_id=?", (CHAT_ID,)).fetchall()
    if not items:
        return await update.message.reply_text("❗ Немає ETF")

    btn = [[InlineKeyboardButton(f"{t} | {'ON' if rb else 'OFF'}", callback_data=f"rebound_toggle:{t}")] for t, rb in items]
    await update.message.reply_text("Оберіть ETF для перемикання Rebound:", reply_markup=InlineKeyboardMarkup(btn))

async def rebound_toggle_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    _, t = q.data.split(":")
    ticker = t.upper()

    row = c.execute("SELECT rebound_enabled FROM subs WHERE ticker=? AND chat_id=?", (ticker, CHAT_ID)).fetchone()
    new_state = 0 if row and row[0] == 1 else 1
    c.execute("UPDATE subs SET rebound_enabled=?, rebound_sent=0 WHERE ticker=? AND chat_id=?", (new_state, ticker, CHAT_ID))
    conn.commit()

    await q.message.reply_text(f"🔁 Rebound для {ticker}: {'ON' if new_state else 'OFF'}")

async def remove_etf_btn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Виклик з меню Remove ETF
    return await list_cmd(update, context)

async def remove_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    _, t = q.data.split(":")
    ticker = t.upper()

    c.execute("DELETE FROM subs WHERE ticker=? AND chat_id=?", (ticker, CHAT_ID))
    conn.commit()
    await q.message.reply_text(f"🗑 {ticker} видалено зі списку")

async def add_input_cmd(update, context):
    await update.message.reply_text("Введіть ticker ETF (приклад: SPY, QQQ, etc):")
    context.user_data["action"]="add"

async def add_handler(update, context):
    ticker = update.message.text.strip().upper()
    price_ago = get_price_1y_ago(ticker) or 0
    c.execute("INSERT OR IGNORE INTO subs(ticker, chat_id, threshold, rebound_enabled, price_ago) VALUES(?,?,?,?,?)", (ticker, CHAT_ID, 5, 1, price_ago))
    conn.commit()
    await update.message.reply_text(f"✔ ETF {ticker} додано у моніторинг")
    context.user_data["action"]=None

async def reply_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()

    if text == "➕ Add ETF":
        return await add_input_cmd(update, context)
    if context.user_data.get("action") == "add":
        return await add_handler(update, context)
    if text == "📌 My ETFs":
        return await list_cmd(update, context)
    if text == "📊 Status":
        items = c.execute("SELECT ticker FROM subs WHERE chat_id=?", (CHAT_ID,)).fetchall()
        if not items:
            return await update.message.reply_text("❗ Немає ETF")
        for (ticker,) in items:
            price_now = get_price_now(ticker)
            ath, _ = get_ath_1y(ticker)
            chart = build_chart_bytes(ticker, ath)
            if chart:
                await update.message.reply_photo(chart, caption=f"{ticker} графік за 365 днів")
        return await status_cmd(update, context)
    if text == "📉 Set Threshold":
        return await threshold_menu(update, context)
    if text == "📈 Toggle Rebound":
        return await rebound_toggle_menu(update, context)
    if text == "🗑 Remove ETF":
        return await remove_etf_btn(update, context)
    if text == "❓ Help":
        return await help_cmd(update, context)
    if text == "📌 Commands":
        return await commands_cmd(update, context)

# ==== REGISTER ====
application = Application.builder().token(TELEGRAM_TOKEN).build()

application.add_handler(CommandHandler("start", start_cmd))
application.add_handler(CommandHandler("help", help_cmd))
application.add_handler(CommandHandler("commands", commands_cmd))
application.add_handler(CommandHandler("list", list_cmd))
application.add_handler(CommandHandler("status", status_cmd))
application.add_handler(CommandHandler("threshold", threshold_menu))
application.add_handler(CommandHandler("rebound", rebound_toggle_menu))

application.add_handler(CallbackQueryHandler(threshold_pick, pattern="^threshold:"))
application.add_handler(CallbackQueryHandler(threshold_save, pattern="^threshold_save:"))
application.add_handler(CallbackQueryHandler(rebound_toggle_handler, pattern="^rebound_toggle:"))
application.add_handler(CallbackQueryHandler(remove_handler, pattern="^remove:"))

application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, reply_router))

threading.Thread(target=monitor_loop, daemon=True).start()
print("Bot running…")
application.run_polling()
