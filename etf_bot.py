import yfinance as yf
import sqlite3
import time
import threading
import io
import matplotlib.pyplot as plt

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters, CallbackQueryHandler

TOKEN = "ТУТ_ТВОЙ_TOKEN"  # <-- встав свій новий токен
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
    top = float(df['Close'].max())
    top_date = df['Close'].idxmax().strftime("%Y-%m-%d")
    return top, top_date

def get_price(ticker):
    df = yf.Ticker(ticker).history(period="1d")
    return float(df['Close'].iloc[-1]) if not df.empty else None

def get_price_1y_ago(ticker):
    df = yf.Ticker(ticker).history(period="365d")
    return float(df['Close'].iloc[0]) if not df.empty else None

def calc_yearly_change(now, ago):
    if now is None or ago is None or ago == 0:
        return None
    return (now - ago) / ago * 100

def build_chart(ticker, top):
    df = yf.Ticker(ticker).history(period="365d")
    hist = df['Close']
    if hist.empty:
        return None
    plt.figure()
    plt.plot(hist)
    plt.axhline(top)
    plt.title(f"{ticker} | TOP 365: {top:.2f}")
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
        rows = c.execute("SELECT ticker, threshold, rebound, last_alert, rebound_sent, top, top_date FROM subs WHERE chat_id=?", (CHAT_ID,)).fetchall()

        for t, th, rb, last, rbs, top, top_date in rows:
            now = get_price(t)
            ago = get_price_1y_ago(t)

            if now is None:
                continue

            if top == 0:
                new_top, new_date = get_top_365(t)
                if new_top:
                    c.execute("UPDATE subs SET top=?, top_date=?, rebound_sent=0 WHERE ticker=? AND chat_id=?", (new_top, new_date, t, CHAT_ID))
                    conn.commit()
                    top, top_date = new_top, new_date
                else:
                    continue

            dd = (top - now) / top * 100
            yearly = calc_yearly_change = calc_yearly_change(now, ago)
            yearly_str = f"{yearly:.2f}%" if yearly is not None else "N/A"

            msg = (
                f"{t.upper()}\n"
                f"Ціна зараз: {now:.2f} USD\n"
                f"Зміна за 365 днів: {yearly_str}\n"
                f"Просадка від TOP 365: {dd:.2f}%\n"
                f"TOP 365: {top:.2f} USD ({top_date})\n"
                f"Поріг alert: {th}% | Rebound: {'ON' if rb else 'OFF'}"
            )

            if dd >= th and last == 0:
                chart = build_chart(t, top)
                if chart:
                    bot.send_photo(chat_id=CHAT_ID, photo=chart, caption="⚠️ Падіння!\n" + msg, parse_mode="Markdown")
                else:
                    bot.send_message(chat_id=CHAT_ID, text="⚠️ Падіння!\n" + msg, parse_mode="Markdown")
                c.execute("UPDATE subs SET last_alert=1, rebound_sent=0 WHERE ticker=? AND chat_id=?", (t, CHAT_ID))
                conn.commit()

            if dd < th and rb == 1 and last == 1 and rbs == 0:
                bot.send_message(chat_id=CHAT_ID, text="📈 Відновлення!\n" + msg, parse_mode="Markdown")
                c.execute("UPDATE subs SET rebound_sent=1 WHERE ticker=? AND chat_id=?", (t, CHAT_ID))
                conn.commit()

            if dd >= th and rbs == 1:
                c.execute("UPDATE subs SET rebound_sent=0 WHERE ticker=? AND chat_id=?", (t, CHAT_ID))
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
        ["❓ Help"]
    ], resize_keyboard=True)
    await update.message.reply_text("Вітаю! Обирайте команду з меню 👇", reply_markup=menu)

async def commands_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = (
        "📌 *Доступні команди:*\n\n"
        "/start — відкрити меню\n"
        "/add <ticker> — додати ETF у моніторинг і підписку\n"
        "/list — переглянути підписані ETF\n"
        "/threshold <ticker> — встановити поріг просадки від TOP 365\n"
        "/rebound <ticker> — ON/OFF сповіщення відновлення для ETF\n"
        "/status — перевірити всі ETF зараз + отримати графіки\n"
        "/commands — список команд\n"
        "/help — опис опцій меню"
    )
    await update.message.reply_text(msg, parse_mode="Markdown")

async def help_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = (
        "🤖 *ETF Monitor Bot — меню та опції:*\n\n"
        "➕ *Add ETF* — додає ETF у моніторинг і підписку на алерти.\n"
        "📌 *My ETFs* — показує всі підписані ETF та їхні пороги просадки.\n"
        "📉 *Set Threshold* — встановлює поріг просадки від річного максимуму (TOP 365d), при якому надсилається сигнал.\n"
        "📈 *Toggle Rebound* — вмикає/вимикає сповіщення про відновлення ціни після просадки, *окремо для кожного ETF*.\n"
        "🔁 *Force Check All* — примусово перевіряє всі ETF негайно і надсилає статус + графіки, якщо є дані.\n"
        "📊 *Status* — показує: поточну ціну, % зміну за 365 днів, TOP 365d і % просадки від TOP.\n"
        "❓ *Help* — показує це пояснення.\n\n"
        "Тікери, які підтримуємо як приклад: `SPY`, `QQQ`, `TLT`"
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

    lines=[]
    for t, top in rows:
        now = get_price_now = get_price(t)
        ago = get_price_1y_ago(t)
        change = calc_yearly_change(now, ago)
        change_str = f"{change:.2f}%" if change else "N/A"
        dd = (top - now) / top * 100 if now and top else None
        lines.append(f"{t.upper()}: {now:.2f} USD | Δ1Y {change_str} | DD {dd:.2f}% | TOP({top})")

    for t, top in rows:
        top_val, _ = get_top_365(t)
        if top_val:
            chart = build_chart(t, top_val)
            if chart:
                await ctx.bot.send_photo(chat_id=CHAT_ID, photo=chart, caption=f"{t.upper()} графік")

    msg="📊 *Status:*\n\n" + ("\n".join(lines) if lines else "немає даних")
    await update.message.reply_text(msg, parse_mode="Markdown")

async def add_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ticker = ctx.args[0].upper() if ctx.args else None
    if not ticker:
        return await update.message.reply_text("❗ Приклад: /add SPY")

    top, d = get_top_365(ticker)
    if not top:
        return await update.message.reply_text("❗ Немає даних або невірний тікер")

    c.execute("INSERT OR IGNORE INTO subs(ticker, chat_id, threshold, rebound, top, top_date, last_alert, rebound_sent) VALUES(?,?,?,?,?,?,0,0)", (ticker, CHAT_ID, 5, 1, top, d))
    conn.commit()
    await update.message.reply_text(f"✅ Додано {ticker}")

async def threshold_btn(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["mode"] = "threshold_menu"
    rows = c.execute("SELECT ticker FROM subs WHERE chat_id=?", (CHAT_ID,)).fetchall()
    if not rows:
        return await update.message.reply_text("📭 Немає ETF")
    btns = [[KeyboardButton(r[0].upper())] for r in rows]
    await update.message.reply_text("Оберіть ETF:", reply_markup=ReplyKeyboardMarkup(btns, resize_keyboard=True))

async def rebound_toggle_btn(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["mode"] = "rebound_menu"
    rows = c.execute("SELECT ticker FROM subs WHERE chat_id=?", (CHAT_ID,)).fetchall()
    if not rows:
        return await update.message.reply_text("📭 Немає ETF")
    btns = [[KeyboardButton(r[0].upper())] for r in rows]
    await update.message.reply_text("Оберіть ETF для Rebound ON/OFF:", reply_markup=ReplyKeyboardMarkup(btns, resize_keyboard=True))

async def reply_router(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip().upper()
    mode = ctx.user_data.get("mode")

    if text == "➕ ADD ETF":
        ctx.user_data["mode"] = "add"
        return await update.message.reply_text("✍ Введіть тікер через: /add SPY")

    if text == "📌 MY ETFS":
        return await list_cmd(update, ctx)

    if text == "📉 SET THRESHOLD":
        return await threshold_btn(update, ctx)

    if text == "📈 TOGGLE REBOUND":
        return await rebound_toggle_btn(update, ctx)

    if text == "🔁 FORCE CHECK ALL":
        return await status_cmd(update, ctx)

    if text == "📊 STATUS":
        return await status_cmd(update, ctx)

    if text == "❓ HELP":
        return await help_cmd(update, ctx)

    # --- Text input flows ---
    if mode == "add":
        ticker = text
        top, d = get_top_365(ticker)
        if not top:
            return await update.message.reply_text("❗ Немає даних")
        c.execute("INSERT OR IGNORE INTO subs(ticker, chat_id, threshold, rebound, top, top_date, last_alert, rebound_sent) VALUES(?,?,?,?,?,?,0,0)", (ticker, CHAT_ID, 5, 1, top, d))
        conn.commit()
        ctx.user_data["mode"] = None
        return await update.message.reply_text(f"✅ Додано {ticker}")

    if mode == "threshold_menu":
        ctx.user_data["ticker"] = text
        ctx.user_data["mode"] = "threshold_value"
        return await update.message.reply_text("✍ Введіть поріг %:")

    if mode == "threshold_value":
        ticker = ctx.user_data.get("ticker")
        try:
            val = float(update.message.text)
            c.execute("UPDATE subs SET threshold=?, rebound=1, rebound_sent=0 WHERE ticker=? AND chat_id=?", (val, ticker, CHAT_ID))
            conn.commit()
            ctx.user_data["mode"] = None
            return await update.message.reply_text(f"🔧 Поріг для {ticker} = {val}%")
        except:
            return await update.message.reply_text("❗ Введіть число")

    if mode == "rebound_menu":
        ticker = text
        row = c.execute("SELECT rebound FROM subs WHERE ticker=? AND chat_id=?", (ticker, CHAT_ID)).fetchone()
        if row:
            new = 0 if row[0] == 1 else 1
            c.execute("UPDATE subs SET rebound=?, rebound_sent=0 WHERE ticker=? AND chat_id=?", (new, ticker, CHAT_ID))
            conn.commit()
            ctx.user_data["mode"] = None
            return await update.message.reply_text(f"🔁 Rebound {ticker}: {'ON' if new else 'OFF'}")
        else:
            return await update.message.reply_text("❗ Немає такого ETF")

    return await update.message.reply_text("Невідома команда. /help")

# --- Run ---
app = Application.builder().token(TOKEN).build()
app.add_handler(CommandHandler("start", start))
app.add_handler(CommandHandler("commands", commands_cmd))
app.add_handler(CommandHandler("list", list_cmd))
app.add_handler(CommandHandler("status", status_cmd))
app.add_handler(CommandHandler("add", add_cmd))
app.add_handler(CommandHandler("threshold", threshold_btn))
app.add_handler(CallbackQueryHandler(threshold_set_handler, pattern="^threshold_set:"))
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, reply_router))

threading.Thread(target=monitor_loop, daemon=True).start()
print("Bot running…")
app.run_polling()
