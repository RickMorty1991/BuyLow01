import yfinance as yf
import sqlite3
import time
import threading
import io
import matplotlib.pyplot as plt
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters, CallbackQueryHandler

TOKEN = "8404794616:AAHUJeJp_wvOa8poUXcZufJRXXC72pZZgU0"  # <-- заміни на валідний токен!
INTERVAL = 600  # 10 хв

# --- DB setup ---
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
def get_top_365(t):
    df = yf.Ticker(t).history(period="365d")
    if df.empty:
        return None, None
    return float(df.Close.max()), df.Close.idxmax().strftime("%Y-%m-%d")

def get_price(t):
    df = yf.Ticker(t).history(period="1d")
    if df.empty:
        return None
    return float(df.Close.iloc[-1])

def get_1y_ago(t):
    df = yf.Ticker(t).history(period="365d")
    if df.empty:
        return None
    return float(df.Close.iloc[0])

def calc_yearly_change(now, ago):
    if now is None or ago is None or ago == 0:
        return None
    return (now - ago) / ago * 100

def build_chart(t, top):
    df = yf.Ticker(t).history(period="365d")
    if df.empty:
        return None
    plt.figure()
    plt.plot(df.Close)
    plt.axhline(top)
    plt.title(f"{t.upper()} | TOP 365d: {top:.2f} USD")
    buf = io.BytesIO()
    plt.savefig(buf, format="png")
    plt.close()
    buf.seek(0)
    return buf

# --- Monitoring loop ---
def monitor_loop(bot):
    while True:
        rows = c.execute("SELECT ticker, threshold, rebound, last_alert, rebound_sent, top, top_date, chat_id FROM subs").fetchall()
        for t, th, rb, last, rbs, top, top_date, chat_id in rows:
            now = get_price(t)
            ago = get_1y_ago(t)
            if now is None:
                continue

            if top == 0:
                new_top, d = get_top_365(t)
                if not new_top:
                    continue
                c.execute("UPDATE subs SET top=?, top_date=?, rebound_sent=0 WHERE ticker=? AND chat_id=?", (new_top, d, t, chat_id))
                conn.commit()
                top, top_date = new_top, d

            dd = (top - now) / top * 100
            yc = calc_yearly_change(now, ago)
            yc_str = f"{yc:.2f}%" if yc is not None else "N/A"

            msg = (
                f"{t.upper()}\n"
                f"Ціна зараз: {now:.2f} USD\n"
                f"Зміна за 365d: {yc_str}\n"
                f"Просадка від TOP 365d: {dd:.2f}%\n"
                f"TOP 365d: {top:.2f} USD ({top_date})\n"
                f"Поріг alert: {th}% | Rebound: {'ON' if rb else 'OFF'}"
            )

            if dd >= th and last == 0:
                chart = build_chart(t, top)
                try:
                    if chart:
                        bot.send_photo(chat_id=chat_id, photo=chart, caption="⚠️ *Просадка!*\n\n" + msg, parse_mode="Markdown")
                    else:
                        bot.send_message(chat_id=chat_id, text="⚠️ *Просадка!*\n\n" + msg, parse_mode="Markdown")
                except Exception as e:
                    print("Send error:", e)
                c.execute("UPDATE subs SET last_alert=1, rebound_sent=0 WHERE ticker=? AND chat_id=?", (t, chat_id))
                conn.commit()

            if dd < th and rb == 1 and last == 1 and rbs == 0:
                try:
                    bot.send_message(chat_id=chat_id, text="📈 *Rebound!*\n\n" + msg, parse_mode="Markdown")
                except Exception as e:
                    print("Send error:", e)
                c.execute("UPDATE subs SET rebound_sent=1 WHERE ticker=? AND chat_id=?", (t, chat_id))
                conn.commit()

            if dd < th and last == 1:
                c.execute("UPDATE subs SET last_alert=0 WHERE ticker=? AND chat_id=?", (t, chat_id))
                conn.commit()

        time.sleep(INTERVAL)

# --- Handlers ---
async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    menu = ReplyKeyboardMarkup([
        ["➕ Add ETF", "📌 My ETFs"],
        ["📉 Set Threshold", "📈 Toggle Rebound"],
        ["🔁 Force Check All", "📊 Status"],
        ["❓ Help", "/commands"]
    ], resize_keyboard=True)
    await update.message.reply_text("Вітаю! Обирайте опцію 👇", reply_markup=menu)

async def help_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = (
        "ℹ *Help Menu*\n\n"
        "➕ Add ETF — додати ETF\n"
        "📌 My ETFs — список ETF\n"
        "📉 Set Threshold — встановити поріг просадки\n"
        "📈 Toggle Rebound — ON/OFF rebound для конкретного ETF\n"
        "🔁 Force Check All — перевірити всі ETF негайно\n"
        "📊 Status — ціна, TOP 365d, DD%, Δ1Y%\n"
        "❓ Help — опис меню\n"
    )
    await update.message.reply_text(msg, parse_mode="Markdown")

async def commands_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = (
        "📌 *Commands List*\n\n"
        "/start — меню\n"
        "/add <ticker> — додати ETF\n"
        "/list — список ETF\n"
        "/threshold <ticker> — встановити поріг\n"
        "/rebound <ticker> — увімк/вимк rebound\n"
        "/status — статус + графік\n"
        "/help — help меню\n"
        "/commands — список команд"
    )
    await update.message.reply_text(msg, parse_mode="Markdown")

async def list_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    rows = c.execute("SELECT ticker, threshold, rebound FROM subs WHERE chat_id=?", (update.message.chat_id,)).fetchall()
    if not rows:
        return await update.message.reply_text("📭 Немає ETF. Додайте через /add SPY")
    msg = "📌 *Ваші ETF:*\n\n" + "\n".join([f"{t.upper()} → поріг {th}% | Rebound: {'ON' if rb else 'OFF'}" for t, th, rb in rows])
    await update.message.reply_text(msg, parse_mode="Markdown")

async def threshold_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    ticker = q.data.split(":")[1].upper()
    btns = [[InlineKeyboardButton(x, callback_data=f"threshold_set:{ticker}:{x.strip('%')}")] for x in ["1%","3%","5%","7%","10%"]]
    await q.message.reply_text("Встановіть поріг:", reply_markup=InlineKeyboardMarkup(btns))

async def threshold_set(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    _, ticker, val = q.data.split(":")
    val = float(val)
    c.execute("UPDATE subs SET threshold=? WHERE ticker=? AND chat_id=?", (val, ticker, q.message.chat_id))
    conn.commit()
    await q.message.reply_text(f"🔧 Threshold for {ticker} = {val}%")

async def rebound_toggle(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ticker = ctx.args[0].upper() if ctx.args else None
    if not ticker:
        return await update.message.reply_text("❗ Приклад: /rebound SPY")
    row = c.execute("SELECT rebound FROM subs WHERE ticker=? AND chat_id=?", (ticker, update.message.chat_id)).fetchone()
    if row:
        new = 0 if row[0] == 1 else 1
        c.execute("UPDATE subs SET rebound=?, rebound_sent=0 WHERE ticker=? AND chat_id=?", (new, ticker, update.message.chat_id))
        conn.commit()
        await update.message.reply_text(f"🔁 Rebound {ticker}: {'ON' if new else 'OFF'}")
    else:
        await update.message.reply_text("❗ Такого ETF немає")

async def force_check(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await status_cmd(update, ctx)

# --- Run ---
app = Application.builder().token(TOKEN).build()
bot = app.bot

app.add_handler(CommandHandler("start", start))
app.add_handler(CommandHandler("help", help_cmd))
app.add_handler(CommandHandler("commands", commands_cmd))
app.add_handler(CommandHandler("list", list_cmd))
app.add_handler(CommandHandler("add", list_cmd))
app.add_handler(CommandHandler("rebound", rebound_toggle))
app.add_handler(CommandHandler("status", force_check))
app.add_handler(CallbackQueryHandler(threshold_set, pattern="^threshold_set:"))
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, reply_router))

threading.Thread(target=monitor_loop, args=(bot,), daemon=True).start()
print("Bot running…")
app.run_polling()
