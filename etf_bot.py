import yfinance as yf

import time

import threading

import io

import sqlite3

import matplotlib.pyplot as plt



from telegram import Bot, Update, ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardButton, InlineKeyboardMarkup

from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters, CallbackQueryHandler



TELEGRAM_TOKEN = "8404794616:AAFM2zf_d3MG5Et89DalcKpu6w6TJbiuWX0"  # <-- впиши новий токен

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



app_bot = Bot(token=TELEGRAM_TOKEN)



# --- Helpers ---

def get_price_now(t):

    df = yf.Ticker(t).history(period="1d")

    return float(df["Close"].iloc[-1]) if not df.empty else None



def get_price_1y_ago(t):

    df = yf.Ticker(t).history(period="365d")

    return float(df["Close"].iloc[0]) if not df.empty else None



def get_ath_1y(t):

    df = yf.Ticker(t).history(period="365d")

    if df.empty:

        return None, None

    return float(df["Close"].max()), df["Close"].idxmax().strftime("%Y-%m-%d")



def calc_change_percent(now, ago):

    return (now - ago) / ago * 100 if ago else None



def build_chart_bytes(t, ath):

    df = yf.Ticker(t).history(period="365d")

    hist = df["Close"]

    if hist.empty:

        return None

    plt.figure()

    plt.plot(hist)

    plt.axhline(ath)

    plt.title(t)

    buf = io.BytesIO()

    plt.savefig(buf, format="png")

    plt.close()

    buf.seek(0)

    return buf



# --- Monitoring Loop ---

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

                f"{ticker}\n"

                f"Ціна: {price_now:.2f} USD\n"

                f"Δ365d: {change:.2f}%\n" if change is not None else

                f"{ticker}\nЦіна: {price_now:.2f} USD\nΔ365d: N/A\n"

            )

            msg += f"📉 DD від ATH 1Y: {dd:.2f}%\n📆 ATH 1Y: {ath:.2f} USD ({ath_date})"



            # Alert падіння 1 раз

            if dd >= threshold and last_alerted == 0:

                chart = build_chart_bytes(ticker, ath)

                if chart:

                    app_bot.send_photo(CHAT_ID, chart, caption="⚠️ *Price Drop Alert!*\n\n" + msg, parse_mode="Markdown")

                else:

                    app_bot.send_message(CHAT_ID, "⚠️ *Price Drop Alert!*\n\n" + msg, parse_mode="Markdown")



                c.execute("UPDATE subs SET last_alerted=1, rebound_sent=0 WHERE ticker=? AND chat_id=?", (ticker, CHAT_ID))

                conn.commit()



            # Rebound 1 раз після падіння

            if dd < threshold and rebound_enabled == 1 and last_alerted == 1 and rebound_sent == 0:

                app_bot.send_message(CHAT_ID, "📈 *Rebound Alert!*\n\n" + msg, parse_mode="Markdown")

                c.execute("UPDATE subs SET rebound_sent=1 WHERE ticker=? AND chat_id=?", (ticker, CHAT_ID))

                conn.commit()



            # Reset rebound_sent якщо знову пробив поріг

            if dd >= threshold and rebound_sent == 1:

                c.execute("UPDATE subs SET rebound_sent=0 WHERE ticker=? AND chat_id=?", (ticker, CHAT_ID))

                conn.commit()



        time.sleep(CHECK_INTERVAL)



# --- Handlers ---

async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):

    keyboard = [

        ["➕ Add ETF", "📌 My ETFs"],

        ["📉 Set Threshold", "📈 Toggle Rebound"],

        ["🔁 Force Check All", "📊 Status"],

        ["🗑 Remove ETF", "❓ Help", "📌 Commands"]

    ]

    menu = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

    await update.message.reply_text("Вітаю! Використовуйте меню 👇", reply_markup=menu)



async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):

    msg = (

        "🤖 *ETF Monitor Bot — Help Menu*\n\n"

        "➕ Add ETF — додати ETF у моніторинг\n"

        "📌 My ETFs — список підписок\n"

        "📉 Set Threshold — встановити поріг просадки від ATH 1Y для 1 ETF\n"

        "📈 Toggle Rebound — увімк/вимк алерти відновлення для 1 ETF\n"

        "🔁 Force Check All — перевірити всі ETF негайно\n"

        "📊 Status — статус всіх ETF\n"

        "🗑 Remove ETF — видалити ETF зі списку\n\n"

        "Команди:\n"

        "/start — меню\n"

        "/list — список ETF\n"

        "/status — статус ETF\n"

        "/remove — видалити ETF\n"

        "/rebound — toggle rebound\n"

        "/subscribe — підписатись на 1 ETF\n"

        "/commands — список команд\n"

        "/help — допомога"

    )

    await update.message.reply_text(msg, parse_mode="Markdown")



async def commands_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):

    msg = (

        "📌 *Список команд:*\n\n"

        "/start — меню\n"

        "/list — список ETF\n"

        "/status — статус ETF\n"

        "/remove — видалити ETF\n"

        "/rebound — Toggle rebound\n"

        "/subscribe — Підписка на 1 ETF\n"

        "/help — Help menu\n"

        "/commands — список команд"

    )

    await update.message.reply_text(msg, parse_mode="Markdown")



async def list_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):

    items = c.execute("SELECT ticker, threshold, rebound_enabled FROM subs WHERE chat_id=?", (CHAT_ID,)).fetchall()

    if not items:

        return await update.message.reply_text("❗ Немає ETF у списку")

    lines = [f"{t} | поріг {th}% | Rebound {'ON' if rb else 'OFF'}" for t, th, rb in items]

    await update.message.reply_text("📌 *Ваші ETF:*\n\n" + "\n".join(lines), parse_mode="Markdown")



async def status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):

    await list_cmd(update, context)



async def threshold_btn(update: Update, context: ContextTypes.DEFAULT_TYPE):

    items = [r[0] for r in c.execute("SELECT ticker FROM subs WHERE chat_id=?", (CHAT_ID,)).fetchall()]

    if not items:

        return await update.message.reply_text("❗ Немає ETF")

    btn = [[InlineKeyboardButton(t, callback_data=f"threshold_set:{t}")] for t in items]

    await update.message.reply_text("Оберіть ETF для встановлення порогу:", reply_markup=InlineKeyboardMarkup(btn))



async def threshold_set(update: Update, context: ContextTypes.DEFAULT_TYPE):

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

    _, t, val = q.data.split(":")

    c.execute("UPDATE subs SET threshold=? WHERE ticker=? AND chat_id=?", (float(val), t, CHAT_ID))

    conn.commit()

    await q.message.reply_text(f"✔ Поріг для {t.upper()} = {val}%")



async def rebound_toggle_btn(update: Update, context: ContextTypes.DEFAULT_TYPE):

    items = [r[0] for r in c.execute("SELECT ticker FROM subs WHERE chat_id=?", (CHAT_ID,)).fetchall()]

    if not items:

        return await update.message.reply_text("❗ Немає ETF")

    btn = [[InlineKeyboardButton(f"{t} | Toggle", callback_data=f"rebound_toggle:{t}")] for t in items]

    await update.message.reply_text("🔁 Оберіть ETF для перемикання Rebound:", reply_markup=InlineKeyboardMarkup(btn))



async def rebound_toggle_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):

    q = update.callback_query

    await q.answer()

    _, ticker = q.data.split(":")

    ticker = ticker.upper()



    row = c.execute("SELECT rebound_enabled FROM subs WHERE ticker=? AND chat_id=?", (ticker, CHAT_ID)).fetchone()

    if row:

        new_state = 0 if row[0] == 1 else 1

        c.execute("UPDATE subs SET rebound_enabled=? WHERE ticker=? AND chat_id=?", (new_state, ticker, CHAT_ID))

        conn.commit()

        await q.message.reply_text(f"Rebound для {ticker}: {'ON' if new_state else 'OFF'}")



async def remove_etf_btn(update: Update, context: ContextTypes.DEFAULT_TYPE):

    items = [r[0] for r in c.execute("SELECT ticker FROM subs WHERE chat_id=?", (CHAT_ID,)).fetchall()]

    if not items:

        return await update.message.reply_text("❗ Немає ETF")

    btn = [[InlineKeyboardButton(f"🗑 {t}", callback_data=f"remove_etf:{t}")] for t in items]

    await update.message.reply_text("Оберіть ETF для видалення:", reply_markup=InlineKeyboardMarkup(btn))



async def remove_etf_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):

    q = update.callback_query

    await q.answer()

    _, ticker = q.data.split(":")

    ticker = ticker.upper()

    c.execute("DELETE FROM subs WHERE ticker=? AND chat_id=?", (ticker, CHAT_ID))

    conn.commit()

    await q.message.reply_text(f"🗑 {ticker} видалено зі списку")



async def reply_router(update: Update, context: ContextTypes.DEFAULT_TYPE):

    text = update.message.text.strip().upper()



    if text == "➕ ADD ETF":

        await update.message.reply_text("Введіть ticker ETF:")

        context.user_data["action"]="add"

        return

    if text == "📌 MY ETFS":

        return await list_cmd(update, context)

    if text == "📉 SET THRESHOLD":

        return await threshold_btn(update, context)

    if text == "📈 TOGGLE REBOUND":

        return await rebound_toggle_btn(update, context)

    if text == "🔁 FORCE CHECK ALL":

        return await status_cmd(update, context)

    if text == "🗑 REMOVE ETF":

        return await remove_etf_btn(update, context)

    if text == "❓ HELP":

        return await help_cmd(update, context)

    if text == "📌 COMMANDS":

        return await commands_cmd(update, context)



    if context.user_data.get("action") == "add":

        ticker = text.upper()

        c.execute("INSERT OR IGNORE INTO subs(ticker, chat_id, threshold, rebound_enabled) VALUES(?,?,5,1)", (ticker, CHAT_ID))

        conn.commit()

        await update.message.reply_text(f"✔ ETF {ticker} додано")

        context.user_data["action"]=None

        return



# --- Register ---

app = Application.builder().token(TELEGRAM_TOKEN).build()

app.add_handler(CommandHandler("start", start_cmd))

app.add_handler(CommandHandler("help", help_cmd))

app.add_handler(CommandHandler("commands", commands_cmd))

app.add_handler(CommandHandler("list", list_cmd))

app.add_handler(CommandHandler("status", status_cmd))

app.add_handler(CallbackQueryHandler(threshold_set, pattern="^threshold_set:"))

app.add_handler(CallbackQueryHandler(threshold_save, pattern="^threshold_save:"))

app.add_handler(CallbackQueryHandler(rebound_toggle_handler, pattern="^rebound_toggle:"))

app.add_handler(CallbackQueryHandler(remove_etf_handler, pattern="^remove_etf:"))

app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, reply_router))



threading.Thread(target=monitor_loop, daemon=True).start()

print("Bot running…")

app.run_polling()
