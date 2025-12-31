import yfinance as yf
import sqlite3
import time
import threading
import io
import matplotlib.pyplot as plt
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters, CallbackQueryHandler

TOKEN = "ВАШ_BOT_TOKEN"  # <-- заміни на валідний токен
DB = "etf_top.db"
INTERVAL = 600  # 10 хв

# --- DB ---
conn = sqlite3.connect(DB, check_same_thread=False)
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

# --- Market ---
def fetch_top(t):
    df = yf.Ticker(t).history("365d")
    if df.empty: return None, None
    return float(df.Close.max()), df.Close.idxmax().strftime("%Y-%m-%d")

def fetch_price(t):
    df = yf.Ticker(t).history("1d")
    return float(df.Close.iloc[-1]) if not df.empty else None

def fetch_ago(t):
    df = yf.Ticker(t).history("365d")
    return float(df.Close.iloc[0]) if not df.empty else None

def make_chart(t, top):
    df = yf.Ticker(t).history("365d")
    hist = df.Close
    if hist.empty: return None
    plt.figure()
    plt.plot(hist)
    plt.axhline(top)
    plt.title(f"{t} | TOP 365: {top:.2f}")
    buf = io.BytesIO()
    plt.savefig(buf, format="png")
    plt.close()
    buf.seek(0)
    return buf

# --- Monitor ---
def monitor():
    bot_app = Application.builder().token(TOKEN).build().bot
    while True:
        rows = c.execute("SELECT ticker, threshold, rebound, last_alert, rebound_sent, top, top_date FROM subs WHERE chat_id=?", (CHAT_ID,)).fetchall()
        for t, th, rb, last, rbs, top, top_date in rows:
            now = fetch_price(t)
            ago = fetch_ago(t)
            if now is None or top == 0:
                new_top, new_date = fetch_top(t)
                if new_top:
                    c.execute("UPDATE subs SET top=?, top_date=?, rebound_sent=0 WHERE ticker=? AND chat_id=?", (new_top, new_date, t, CHAT_ID))
                    conn.commit()
                    top, top_date = new_top, new_date
                else:
                    continue

            dd = (top - now) / top * 100
            chg = ((now - ago) / ago * 100) if ago else None
            chg_str = f"{chg:.2f}%" if chg is not None else "N/A"

            text = (
                f"{t.upper()}\n"
                f"Ціна зараз: {now:.2f} USD\n"
                f"Зміна за 365 днів: {chg_str}\n"
                f"Просадка від TOP 365: {dd:.2f}%\n"
                f"TOP 365: {top:.2f} USD ({top_date})\n"
                f"Поріг alert: {th}% | Rebound: {'ON' if rb else 'OFF'}"
            )

            if dd >= th and last == 0:
                chart = make_chart(t, top)
                if chart:
                    bot_app.send_photo(chat_id=CHAT_ID, photo=chart, caption="⚠️ Падіння!\n" + text)
                else:
                    bot_app.send_message(chat_id=CHAT_ID, text="⚠️ Падіння!\n" + text)
                c.execute("UPDATE subs SET last_alert=1, rebound_sent=0 WHERE ticker=? AND chat_id=?", (t, CHAT_ID))
                conn.commit()

            if dd < th and rb == 1 and last == 1 and rbs == 0:
                bot_app.send_message(chat_id=CHAT_ID, text="📈 Відновлення!\n" + text)
                c.execute("UPDATE subs SET rebound_sent=1 WHERE ticker=? AND chat_id=?", (t, CHAT_ID))
                conn.commit()

            if dd < th and last == 1:
                c.execute("UPDATE subs SET last_alert=0 WHERE ticker=? AND chat_id=?", (t, CHAT_ID))
                conn.commit()

        time.sleep(INTERVAL)

# --- Handlers ---
async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Привіт! Введи /help щоб побачити команди.")

async def help_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = (
        "📌 *Команди бота:*\n\n"
        "/add <ticker> — додати ETF у моніторинг\n"
        "/list — список ваших ETF\n"
        "/status — перевірка всіх ETF зараз + графіки\n"
        "/rebound <ticker> — ON/OFF алерти відновлення\n"
        "/threshold <ticker> — встановити поріг просадки (кнопками)\n"
        "/commands — показати всі команди\n"
        "/help — допомога"
    )
    await update.message.reply_text(msg, parse_mode="Markdown")

async def list_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    rows = c.execute("SELECT ticker, threshold, rebound FROM subs WHERE chat_id=?", (CHAT_ID,)).fetchall()
    if not rows:
        return await update.message.reply_text("📭 Немає підписок")
    lines = [f"{t.upper()} → поріг {th}% | Rebound: {'ON' if rb else 'OFF'}" for t, th, rb in rows]
    await update.message.reply_text("📌 *Ваші ETF:*\n\n" + "\n".join(lines), parse_mode="Markdown")

async def status_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    rows = c.execute("SELECT ticker, top FROM subs WHERE chat_id=?", (CHAT_ID,)).fetchall()
    if not rows:
        return await update.message.reply_text("📭 Немає даних")
    for t, top in rows:
        chart = make_chart(t, top)
        if chart:
            await ctx.bot.send_photo(chat_id=CHAT_ID, photo=chart, caption=f"{t.upper()} графік")
    await update.message.reply_text("🔁 Статус надіслано")

async def add_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    t = ctx.args[0].upper() if ctx.args else None
    if not t:
        return await update.message.reply_text("❗ Вкажи тікер. Приклад: /add SPY")
    top, d = fetch_top(t)
    if top:
        c.execute("INSERT OR IGNORE INTO subs(ticker, chat_id, threshold, rebound, top, top_date) VALUES(?,?,?,?,?,?)", (t, CHAT_ID, 5, 1, top, d))
        conn.commit()
        await update.message.reply_text(f"✅ Додано {t}")
    else:
        await update.message.reply_text("❗ Не вдалося отримати дані для тікера")

async def threshold_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    t = ctx.args[0].upper() if ctx.args else None
    if not t:
        return await update.message.reply_text("❗ Вкажи тікер. Приклад: /threshold QQQ")
    row = c.execute("SELECT ticker FROM subs WHERE ticker=? AND chat_id=?", (t, CHAT_ID)).fetchone()
    if not row:
        return await update.message.reply_text("❗ Такого ETF немає. Додай через /add")
    buttons = [[InlineKeyboardButton(x, callback_data=f"threshold_set:{t}:{x.strip('%')}")] for x in ["1%","3%","5%","7%","10%"]]
    await update.message.reply_text("Встановіть поріг просадки:", reply_markup=InlineKeyboardMarkup(buttons))

async def threshold_set_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    _, t, val = q.data.split(":")
    val = float(val)
    c.execute("UPDATE subs SET threshold=?, rebound=1, rebound_sent=0 WHERE ticker=? AND chat_id=?", (val, t, CHAT_ID))
    conn.commit()
    await q.message.reply_text(f"🔧 Поріг для {t} = {val}%")

async def rebound_toggle(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    t = ctx.args[0].upper() if ctx.args else None
    if not t:
        return await update.message.reply_text("❗ Вкажи тікер. Приклад: /rebound TLT")
    row = c.execute("SELECT rebound FROM subs WHERE ticker=? AND chat_id=?", (t, CHAT_ID)).fetchone()
    if row:
        new = 0 if row[0] == 1 else 1
        c.execute("UPDATE subs SET rebound=?, rebound_sent=0 WHERE ticker=? AND chat_id=?", (new, t, CHAT_ID))
        conn.commit()
        await update.message.reply_text(f"🔁 Rebound для {t}: {'ON' if new else 'OFF'}")
    else:
        await update.message.reply_text("❗ Такого ETF немає")

# --- Run ---
app = Application.builder().token(TOKEN).build()
app.add_handler(CommandHandler("start", start))
app.add_handler(CommandHandler("help", help_cmd))
app.add_handler(CommandHandler("list", list_cmd))
app.add_handler(CommandHandler("status", status_cmd))
app.add_handler(CommandHandler("add", add_cmd))
app.add_handler(CommandHandler("threshold", threshold_cmd))
app.add_handler(CommandHandler("rebound", rebound_toggle))
app.add_handler(CallbackQueryHandler(threshold_set_handler))
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, reply_router))

threading.Thread(target=monitor, daemon=True).start()
print("Bot running…")
app.run_polling()
