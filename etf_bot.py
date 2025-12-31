import yfinance as yf
import sqlite3
import time
import threading
import io
import matplotlib.pyplot as plt

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters, CallbackQueryHandler

# ==== CONFIG ====
TOKEN = "8404794616:AAHUJeJp_wvOa8poUXcZufJRXXC72pZZgU0"
CHAT_ID = 409544912
INTERVAL = 600  # 10 хв

# ==== DATABASE ====
db = sqlite3.connect("etf_top.db", check_same_thread=False)
c = db.cursor()
c.execute("""
CREATE TABLE IF NOT EXISTS subs(
    ticker TEXT,
    chat_id INTEGER,
    threshold REAL DEFAULT 5.0,
    rebound_enabled INTEGER DEFAULT 1,
    last_alerted INTEGER DEFAULT 0,
    rebound_sent INTEGER DEFAULT 0,
    price_ago REAL DEFAULT 0,
    PRIMARY KEY (ticker, chat_id)
)
""")
db.commit()

# ==== FINANCE HELPERS ====
def price_now(t):
    df = yf.Ticker(t).history(period="1d")
    return float(df["Close"].iloc[-1]) if not df.empty else None

def price_ago(t):
    df = yf.Ticker(t).history(period="1y")
    return float(df["Close"].iloc[0]) if not df.empty else None

def ath_1y(t):
    df = yf.Ticker(t).history(period="1y")
    if df.empty:
        return None, None
    return float(df["Close"].max()), df["Close"].idxmax().strftime("%Y-%m-%d")

def change_pct(n, a):
    return (n - a) / a * 100 if a else None

def chart_buf(t, a):
    df = yf.Ticker(t).history(period="1y")
    h = df["Close"]
    if h.empty or a is None:
        return None
    plt.figure()
    plt.plot(h)
    plt.axhline(a)
    plt.title(f"{t} | ATH 1Y {a:.2f} USD")
    b = io.BytesIO()
    plt.savefig(b, format="png")
    plt.close()
    b.seek(0)
    return b

# ==== MONITOR LOOP ====
def monitor():
    while True:
        rows = c.execute("SELECT ticker, threshold, rebound_enabled, last_alerted, rebound_sent, price_ago FROM subs WHERE chat_id=?", (CHAT_ID,)).fetchall()

        for t, th, rb, la, rs, pa in rows:
            n = price_now(t)
            x, d = ath_1y(t)
            if n is None or x is None:
                continue
            dd = (x - n) / x * 100
            c365 = change_pct(n, pa) if pa else None

            msg = f"{t}: {n:.2f} USD\nΔ365d: {c365:.2f}%\nDD1Y: {dd:.2f}% ({d})" if c365 else f"{t}: {n:.2f} USD\nΔ365d: N/A\nDD1Y: {dd:.2f}% ({d})"

            if dd >= th and not la:
                buf = chart_buf(t, x)
                if buf:
                    application.bot.send_photo(CHAT_ID, buf, caption="⚠️ Просадка!\n\n" + msg)
                else:
                    application.bot.send_message(CHAT_ID, "⚠️ Просадка!\n\n" + msg)
                c.execute("UPDATE subs SET last_alerted=1, rebound_sent=0 WHERE ticker=? AND chat_id=?", (t, CHAT_ID))
                db.commit()

            if dd < th and rb and la and not rs:
                application.bot.send_message(CHAT_ID, "📈 Відновлення!\n\n" + msg)
                c.execute("UPDATE subs SET rebound_sent=1 WHERE ticker=? AND chat_id=?", (t, CHAT_ID))
                db.commit()

            if dd >= th and rs:
                c.execute("UPDATE subs SET rebound_sent=0 WHERE ticker=? AND chat_id=?", (t, CHAT_ID))
                db.commit()

        time.sleep(INTERVAL)

# ==== BOT HANDLERS ====
async def start(u, cx):
    kb = [["➕ Add ETF", "📌 My ETFs"], ["📉 Set Threshold", "📈 Toggle Rebound"], ["🔁 Force Check All", "📊 Status"], ["❓ Help", "📌 Commands"]]
    await u.message.reply_text("Меню 👇", reply_markup=ReplyKeyboardMarkup(kb, resize_keyboard=True))

async def help_menu(u, cx):
    txt = (
        "📘 *Help Menu*\n\n"
        "➕ *Add ETF* — додати ETF у моніторинг\n"
        "📌 *My ETFs* — список ваших ETF та видалення\n"
        "📉 *Set Threshold* — поріг просадки від річного ATH\n"
        "📈 *Toggle Rebound* — увімк/вимк сигнал відновлення ціни\n"
        "🔁 *Force Check All* — негайна перевірка всіх ETF\n"
        "📊 *Status* — поточна ціна + графіки\n"
        "🗑 *Remove ETF* — видалення через My ETFs\n"
        "📌 *Commands* — список усіх команд\n"
    )
    await u.message.reply_text(txt, parse_mode="Markdown")

async def commands_menu(u, cx):
    txt = (
        "🧾 *Список команд:*\n\n"
        "/start — меню\n"
        "/list — список ETF\n"
        "/status — статус ETF\n"
        "/threshold SPY 4 — поріг просадки 4%\n"
        "/rebound QQQ OFF — вимкнути rebound для QQQ\n"
        "/remove SPY — видалити SPY\n"
        "/help — довідка меню\n"
        "/commands — список команд"
    )
    await u.message.reply_text(txt, parse_mode="Markdown")

async def list_etfs(u, cx):
    rows = c.execute("SELECT ticker, threshold, rebound_enabled FROM subs WHERE chat_id=?", (CHAT_ID,)).fetchall()
    if not rows:
        return await u.message.reply_text("❗ Немає ETF")

    btns = [[InlineKeyboardButton(f"{t} | 🗑 Remove", callback_data=f"remove:{t}")] for t, _, _ in rows]
    await u.message.reply_text("📌 *My ETFs:* 👇", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(btns))

async def remove_cb(u, cx):
    q = u.query
    await q.answer()
    t = q.data.split(":")[1].upper()
    c.execute("DELETE FROM subs WHERE ticker=? AND chat_id=?", (t, CHAT_ID))
    db.commit()
    await q.message.reply_text(f"🗑 {t} видалено")
    await list_etfs(u, cx)

async def status_all(u, cx):
    rows = c.execute("SELECT ticker FROM subs WHERE chat_id=?", (CHAT_ID,)).fetchall()
    if not rows:
        return await u.message.reply_text("❗ Немає ETF")

    for (t,) in rows:
        n = price_now(t)
        x, d = ath_1y(t)
        a = price_ago(t)
        c365 = change_pct(n, a) if n and a else None

        text = f"📊 {t}\n💰 Ціна: {n:.2f} USD\nΔ365d: {c365:.2f}%\nATH1Y: {x:.2f} ({d})" if c365 else f"📊 {t}\n💰 Ціна: {n:.2f} USD\nΔ365d: N/A\nATH1Y: {x:.2f} ({d})"

        buf = chart_buf(t, x)
        if buf:
            await u.message.reply_photo(buf, caption=text)
        else:
            await u.message.reply_text(text)

    await u.message.reply_text("📊 Статус оновлено ✔")

async def force_check(u, cx):
    await u.message.reply_text("🔍 Перевіряю зараз…")
    threading.Thread(target=monitor_once, daemon=True).start()

def monitor_once():
    rows = c.execute("SELECT ticker FROM subs WHERE chat_id=?", (CHAT_ID,)).fetchall()
    for (t,) in rows:
        x, _ = ath_1y(t)
        buf = chart_buf(t, x)
        if buf:
            bot.send_photo(CHAT_ID, buf, caption=f"{t} check OK")

# ==== APP START ====
application = Application.builder().token(TOKEN).build()

application.add_handler(CommandHandler("start", start))
application.add_handler(CommandHandler("list", list_etfs))
application.add_handler(CommandHandler("help", help_menu))
application.add_handler(CommandHandler("commands", commands_menu))
application.add_handler(CommandHandler("status", status_all))
application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, reply_router))
application.add_handler(CallbackQueryHandler(remove_cb, pattern="^remove:"))

threading.Thread(target=monitor, daemon=True).start()
print("Bot running…")
application.run_polling()
