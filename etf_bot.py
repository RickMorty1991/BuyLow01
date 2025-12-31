import yfinance as yf
import sqlite3
import time
import threading
import io
import matplotlib.pyplot as plt

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters, CallbackQueryHandler

TOKEN = "8404794616:AAGJ-b-SMbG1dcFOgTxd5Qs91VgddjL7rWc"  # <-- встав новий токен
CHAT_ID = 409544912
INTERVAL = 600  # 10 хв

# --- Database ---
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
    return float(df.Close.max()), df.Close.idxmax().strftime("%Y-%m-%d")

def get_price(ticker):
    df = yf.Ticker(ticker).history(period="1d")
    return float(df.Close.iloc[-1]) if not df.empty else None

def get_price_1y_ago(ticker):
    df = yf.Ticker(ticker).history(period="365d")
    return float(df.Close.iloc[0]) if not df.empty else None

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
    plt.title(f"{ticker} | TOP 365d: {top:.2f}")
    buf = io.BytesIO()
    plt.savefig(buf, format="png")
    plt.close()
    buf.seek(0)
    return buf

# --- Monitoring loop ---
def monitor_loop():
    while True:
        rows = c.execute("SELECT ticker, threshold, rebound, last_alert, rebound_sent, top, top_date FROM subs WHERE chat_id=?", (CHAT_ID,)).fetchall()
        for t, th, rb, last, rbs, top, top_date in rows:
            now = get_price(t)
            ago = get_price_1y_ago(t)
            if now is None:
                continue

            if top == 0:
                new_top, new_date = get_top_365(t)
                if new_top:
                    c.execute("UPDATE subs SET top=?, top_date=? WHERE ticker=? AND chat_id=?", (new_top, new_date, t, CHAT_ID))
                    conn.commit()
                    top, top_date = new_top, new_date
                else:
                    continue

            dd = (top - now) / top * 100
            change = calc_yearly_change(now, ago)
            change_str = f"{change:.2f}%" if change is not None else "N/A"

            msg = (
                f"{t.upper()}\n"
                f"Ціна зараз: {now:.2f} USD\n"
                f"Зміна за 365 днів: {change_str}\n"
                f"Просадка від TOP 365d: {dd:.2f}%\n"
                f"TOP 365d: {top:.2f} USD ({top_date})\n"
                f"Поріг alert: {th}% | Rebound: {'ON' if rb else 'OFF'}"
            )

            if dd >= th and last == 0:
                try:
                    chart = build_chart(t, top)
                    if chart:
                        bot.send_photo(chat_id=CHAT_ID, photo=chart, caption="⚠️ Падіння!\n" + msg, parse_mode="Markdown")
                    else:
                        bot.send_message(chat_id=CHAT_ID, text="⚠️ Падіння!\n" + msg, parse_mode="Markdown")
                except Exception as e:
                    print("Send error:", e)
                c.execute("UPDATE subs SET last_alert=1, rebound_sent=0 WHERE ticker=? AND chat_id=?", (t, CHAT_ID))
                conn.commit()

            if dd < th and rb == 1 and last == 1 and rbs == 0:
                try:
                    bot.send_message(chat_id=CHAT_ID, text="📈 Відновлення!\n" + msg, parse_mode="Markdown")
                except Exception as e:
                    print("Send error:", e)
                c.execute("UPDATE subs SET rebound_sent=1 WHERE ticker=? AND chat_id=?", (t, CHAT_ID))
                conn.commit()

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
        ["❓ Help", "/commands"]
    ], resize_keyboard=True)
    await update.message.reply_text("Вітаю! Обирайте команду з меню 👇", reply_markup=menu)

async def commands_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📌 *Доступні команди:*\n\n"
        "/start — меню\n"
        "/add <ticker> — додати ETF\n"
        "/list — список ETF\n"
        "/threshold <ticker> — поріг просадки (кнопки 1/3/5/7/10%)\n"
        "/rebound <ticker> — ON/OFF відновлення\n"
        "/status — перевірити всі ETF\n"
        "/commands — список команд\n"
        "/help — допомога"
    , parse_mode="Markdown")

async def help_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "ℹ Бот моніторить ETF від річного максимуму (TOP 365d).\n"
        "📉 Алерт спрацьовує при просадці ≥ порогу.\n"
        "🔔 Rebound ON/OFF працює для кожного ETF.\n\n"
        "Приклади: SPY, QQQ, TLT"
    )

async def list_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    rows = c.execute("SELECT ticker, threshold, rebound FROM subs WHERE chat_id=?", (CHAT_ID,)).fetchall()
    if not rows:
        return await update.message.reply_text("📭 Немає ETF у моніторингу")
    msg = "📌 *Ваші ETF:*\n\n" + "\n".join([f"{t.upper()} → поріг {th}% | Rebound: {'ON' if rb else 'OFF'}" for t, th, rb in rows])
    await update.message.reply_text(msg, parse_mode="Markdown")

async def status_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    rows = c.execute("SELECT ticker, top FROM subs WHERE chat_id=?", (CHAT_ID,)).fetchall()
    if not rows:
        return await update.message.reply_text("📭 Немає даних")
    lines=[]
    for t, top in rows:
        now = get_price(t)
        ago = get_price_1y_ago(t)
        change = calc_yearly_change(now, ago)
        change_str = f"{change:.2f}%" if change else "N/A"
        dd = (top - now) / top * 100 if now and top else None
        lines.append(f"{t.upper()}: {now:.2f} USD | Δ1Y {change_str} | DD {dd:.2f}%")

    for t, top in rows:
        top_val, _ = get_top_365(t)
        if top_val:
            chart = build_chart(t, top_val)
            if chart:
                await ctx.bot.send_photo(chat_id=CHAT_ID, photo=chart, caption=f"{t.upper()} графік")

    await update.message.reply_text("📊 *Status:*\n\n" + "\n".join(lines), parse_mode="Markdown")

async def add_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ticker = ctx.args[0].upper() if ctx.args else None
    if not ticker:
        return await update.message.reply_text("❗ Приклад: /add SPY")
    top, d = get_top_365(ticker)
    if not top:
        return await update.message.reply_text("❗ Немає даних або невірний тікер")
    c.execute("INSERT OR IGNORE INTO subs(ticker, chat_id, threshold, rebound, top, top_date) VALUES(?,?,?,?,?,?)", (ticker, CHAT_ID, 5, 1, top, d))
    conn.commit()
    await update.message.reply_text(f"✅ Додано {ticker}")

async def threshold_btn(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ticker = update.callback_query.data.split(":")[1].upper()

async def rebound_toggle_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ticker = update.callback_query.data.split(":")[1].upper()

async def set_threshold_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    _, ticker, val = q.data.split(":")
    val = float(val)
    c.execute("UPDATE subs SET threshold=? WHERE ticker=? AND chat_id=?", (val, ticker, CHAT_ID))
    conn.commit()
    await q.message.reply_text(f"🔧 Поріг для {ticker} = {val}%")

async def rebound_toggle_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    _, ticker = q.data.split(":")
    ticker = ticker.upper()
    row = c.execute("SELECT rebound FROM subs WHERE ticker=? AND chat_id=?", (ticker, CHAT_ID)).fetchone()
    if row:
        new = 0 if row[0] == 1 else 1
        c.execute("UPDATE subs SET rebound=?, rebound_sent=0 WHERE ticker=? AND chat_id=?", (new, ticker, CHAT_ID))
        conn.commit()
        await q.message.reply_text(f"🔁 Rebound для {ticker}: {'ON' if new else 'OFF'}")

# --- Callback Menus ---
async def threshold_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    _, ticker = q.data.split(":")
    ticker = ticker.upper()
    btns = [[InlineKeyboardButton(x, callback_data=f"threshold_set:{ticker}:{x.strip('%')}")] for x in ["1%","3%","5%","7%","10%"]]
    await q.message.reply_text(f"Встановіть поріг для {ticker}:", reply_markup=InlineKeyboardMarkup(btns))

async def rebound_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    _, ticker = q.data.split(":")
    ticker = ticker.upper()
    c.execute("SELECT rebound FROM subs WHERE ticker=? AND chat_id=?", (ticker, CHAT_ID))
    row = c.fetchone()
    new = 0 if row and row[0] == 1 else 1
    c.execute("UPDATE subs SET rebound=?, rebound_sent=0 WHERE ticker=? AND chat_id=?", (new, ticker, CHAT_ID))
    conn.commit()
    await q.message.reply_text(f"🔁 Rebound для {ticker}: {'ON' if new else 'OFF'}")

# --- Router ---
async def reply_router(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if text == "➕ Add ETF":
        return await update.message.reply_text("Введіть ETF через: /add SPY")
    if text == "📌 My ETFs":
        return await list_cmd(update, ctx)
    if text == "📉 Set Threshold":
        rows = c.execute("SELECT ticker FROM subs WHERE chat_id=?", (CHAT_ID,)).fetchall()
        btns=[[InlineKeyboardButton(r[0].upper(),callback_data=f"threshold:{r[0].upper()}")] for r in rows]
        return await update.message.reply_text("Оберіть ETF:", reply_markup=InlineKeyboardMarkup(btns))
    if text == "📈 Toggle Rebound":
        rows = c.execute("SELECT ticker FROM subs WHERE chat_id=?", (CHAT_ID,)).fetchall()
        btns=[[InlineKeyboardButton(r[0].upper(),callback_data=f"rebound:{r[0].upper()}")] for r in rows]
        return await update.message.reply_text("Оберіть ETF:", reply_markup=InlineKeyboardMarkup(btns))
    if text == "🔁 Force Check All" or text == "📊 Status":
        return await status_cmd(update, ctx)
    if text == "❓ Help":
        return await help_cmd(update, ctx)
    if text == "/commands":
        return await commands_cmd(update, ctx)
    await update.message.reply_text("Невідома команда. /help")

# --- Run ---
app = Application.builder().token(TOKEN).build()
app.add_handler(CommandHandler("start", start))
app.add_handler(CommandHandler("commands", commands_cmd))
app.add_handler(CommandHandler("list", list_cmd))
app.add_handler(CommandHandler("status", status_cmd))
app.add_handler(CallbackQueryHandler(threshold_menu, pattern="^threshold:"))
app.add_handler(CallbackQueryHandler(rebound_menu, pattern="^rebound:"))
app.add_handler(CallbackQueryHandler(set_threshold_handler, pattern="^threshold_set:"))
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, reply_router))

threading.Thread(target=monitor_loop, daemon=True).start()
print("Bot running…")
app.run_polling()
