import yfinance as yf
import sqlite3
import time
import threading
import io
import matplotlib.pyplot as plt
from telegram import Bot, Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, ContextTypes, CallbackQueryHandler, MessageHandler, filters, CallbackQuery

TELEGRAM_TOKEN = "ВАШ_BOT_TOKEN"  # ⚠️ заміни на новий після створення
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
    PRIMARY KEY (ticker, chat_id)
)""")
conn.commit()

bot = Bot(token=TELEGRAM_TOKEN)

# --- Data helpers ---
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
    ath = float(df['Close'].max())
    ath_date = df['Close'].idxmax().strftime("%Y-%m-%d")
    return ath, ath_date

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
def monitor_prices():
    while True:
        c.execute("SELECT ticker, threshold, rebound_enabled FROM subs WHERE chat_id=?", (CHAT_ID,))
        items = c.fetchall()

        for t, threshold, rebound_enabled in items:
            price_now = get_price(t)
            price_ago = get_price_1y_ago(t)
            ath, ath_date = get_ath_1y(t)

            if price_now is None or ath is None:
                continue

            dd = (ath - price_now) / ath * 100
            change_1y = calc_change_percent(price_now, price_ago)
            yearly_str = f"Δ 1Y: {change_1y:.2f}%" if change_1y is not None else "Δ 1Y: N/A"

            msg = (
                f"{t}: {price_now:.2f} USD\n"
                f"{yearly_str}\n"
                f"Просадка від ATH 1Y: {dd:.2f}%\n"
                f"ATH 1Y: {ath:.2f} ({ath_date})\n"
                f"Поріг alert: {threshold}%\n"
                f"Rebound: {'ON' if rebound_enabled==1 else 'OFF'}"
            )

            # Алерт падіння 1 раз за пробій порогу
            if dd >= threshold:
                chart = build_chart_bytes(t, ath)
                if chart:
                    bot.send_photo(chat_id=CHAT_ID, photo=chart, caption="⚠️ Падіння!\n" + msg)
                else:
                    bot.send_message(chat_id=CHAT_ID, text="⚠️ Падіння!\n" + msg)

            # Rebound alert 1 раз, якщо увімкнено
            if dd < threshold and rebound_enabled == 1:
                bot.send_message(chat_id=CHAT_ID, text="📈 Відновлення!\n" + msg)
                c.execute("UPDATE subs SET rebound_enabled=0 WHERE ticker=? AND chat_id=?", (t, CHAT_ID))
                conn.commit()

            # Якщо знову пробило поріг — скидати rebound flag
            if dd >= threshold and rebound_enabled == 0:
                c.execute("UPDATE subs SET rebound_enabled=1 WHERE ticker=? AND chat_id=?", (t, CHAT_ID))
                conn.commit()

        time.sleep(CHECK_INTERVAL)

# --- Menu UI ---
async def show_main_menu(update: Update):
    c.execute("SELECT ticker, threshold, rebound_enabled FROM subs WHERE chat_id=?", (CHAT_ID,))
    items = c.fetchall()

    rows = [
        [InlineKeyboardButton(f"{t} | Alert@{th}% | Rebound: {'ON' if rb==1 else 'OFF'}", callback_data="none")]
        for t, th, rb in items
    ]

    keyboard = rows + [
        [InlineKeyboardButton("➕ Add ETF", callback_data="add")],
        [InlineKeyboardButton("🗑 Remove ETF", callback_data="remove")],
        [InlineKeyboardButton("📉 Set Threshold", callback_data="threshold")],
        [InlineKeyboardButton("📈 Toggle Rebound", callback_data="rebound_menu")],
        [InlineKeyboardButton("📊 Status", callback_data="status")],
        [InlineKeyboardButton("❓ Help", callback_data="help")]
    ]

    if update.message:
        await update.message.reply_text("ETF Monitor Bot 🚀", reply_markup=InlineKeyboardMarkup(keyboard))
    else:
        await update.callback_query.message.reply_text("Меню:", reply_markup=InlineKeyboardMarkup(keyboard))

async def add_btn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    await q.message.reply_text("Введіть тікер ETF для додавання:")
    context.user_data['action'] = "add_input"

async def threshold_btn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    await q.message.reply_text("Оберіть ETF:", reply_markup=await make_etf_buttons("threshold_select"))
    context.user_data['action'] = "threshold_select"

async def rebound_menu_btn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    await q.message.reply_text("Оберіть ETF для Rebound ON/OFF:", reply_markup=await make_etf_buttons("toggle_rebound"))
    context.user_data['action'] = "toggle_rebound"

async def remove_btn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    await q.message.reply_text("Оберіть ETF для видалення:", reply_markup=await make_etf_buttons("remove_select"))

async def status_btn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    c.execute("SELECT ticker, threshold, rebound_enabled FROM subs WHERE chat_id=?", (CHAT_ID,))
    items = c.fetchall()

    lines=[]
    charts=[]
    for t, th, rb in items:
        price_now = get_price(t)
        price_ago = get_price_1y_ago(t)
        ath, ath_date = get_ath_1y(t)
        if price_now and ath:
            dd = (ath - price_now) / ath * 100
            change = calc_change_percent(price_now, price_ago)
            change_str = f"Δ 1Y: {change:.2f}%" if change is not None else "Δ 1Y: N/A"
            lines.append(f"{t}: {price_now:.2f} USD | {change_str} | DD {dd:.2f}% | Rebound {'ON' if rb else 'OFF'} | Alert@{th}%")
            chart = build_chart_bytes(t, ath)
            if chart:
                charts.append(chart)

    msg="📊 Статус ETF:\n" + ("\n".join(lines) if lines else "немає даних")

    for chart in charts:
        bot.send_photo(chat_id=CHAT_ID, photo=chart)
    await q.message.reply_text(msg)

async def help_btn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    msg = (
        "🤖 *ETF Monitor Bot — опис меню:*\n\n"
        "➕ *Add ETF* — додати новий ETF у моніторинг і підписку.\n"
        "📌 *My ETFs* — переглянути ваш список ETF.\n"
        "📉 *Set Threshold* — вибрати ETF і встановити поріг просадки кнопками (1/3/5/7/10%).\n"
        "📈 *Toggle Rebound* — увімкнути або вимкнути сповіщення про відновлення для обраного ETF.\n"
        "📊 *Status* — подивитись: ціну зараз, % зміну vs 365 днів тому, просадку від ATH 1Y, дату ATH, поріг та rebound стан.\n"
        "🔁 *Force Check Now* — перевірити *всі* ETF негайно.\n"
        "❓ *Help* — показати опис меню і команд."
    )
    await q.message.reply_text(msg, parse_mode="Markdown")

# Builders for ticker lists
async def make_etf_buttons(prefix):
    c.execute("SELECT ticker FROM subs WHERE chat_id=?", (CHAT_ID,))
    items = [r[0] for r in c.fetchall()]
    if not items:
        return InlineKeyboardMarkup([[InlineKeyboardButton("немає ETF", callback_data="none")]])

    keyboard = [[InlineKeyboardButton(t, callback_data=f"{prefix}:{t}")] for t in items]
    keyboard.append([InlineKeyboardButton("🔙 Back", callback_data="back")])
    return InlineKeyboardMarkup(keyboard)

async def etf_action_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    prefix, ticker = q.data.split(":")
    ticker = ticker.upper()

    if prefix == "remove_select":
        c.execute("DELETE FROM subs WHERE ticker=? AND chat_id=?", (ticker, CHAT_ID))
        conn.commit()
        await q.message.reply_text(f"🗑 Видалено {ticker}")
        return await show_main_menu(update)

    if prefix == "threshold_select":
        context.user_data['ticker'] = ticker
        context.user_data['action'] = "threshold_pick"
        buttons = [[InlineKeyboardButton(x, callback_data=f"threshold_set:{ticker}:{x.strip('%')}")] for x in ["1%","3%","5%","7%","10%"]]
        await q.message.reply_text("Встановіть поріг просадки:", reply_markup=InlineKeyboardMarkup(buttons))
        return

    if prefix == "toggle_rebound":
        c.execute("SELECT rebound_enabled FROM subs WHERE ticker=? AND chat_id=?", (ticker, CHAT_ID))
        row = c.fetchone()
        if row:
            new_state = 0 if row[0] == 1 else 1
            c.execute("UPDATE subs SET rebound_enabled=? WHERE ticker=? AND chat_id=?", (new_state, ticker, CHAT_ID))
            conn.commit()
            await q.message.reply_text(f"🔁 Rebound для {ticker}: {'ON' if new_state else 'OFF'}")
        return await show_main_menu(update)

async def threshold_set_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    _, ticker, val = q.data.split(":")
    val = float(val)
    c.execute("UPDATE subs SET threshold=?, rebound_enabled=1 WHERE ticker=? AND chat_id=?", (val, ticker, CHAT_ID))
    conn.commit()
    await q.message.reply_text(f"🔧 Новий поріг для {ticker} = {val}%")
    await show_main_menu(update)

# Text router for Add
async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data.get('action') == "add_input":
        ticker = update.message.text.strip().upper()
        c.execute("INSERT OR IGNORE INTO subs(ticker, chat_id, threshold, rebound_enabled) VALUES(?,?,5,1)", (ticker, CHAT_ID))
        conn.commit()
        await update.message.reply_text(f"✅ Додано {ticker}")
        context.user_data['action'] = None
        await show_main_menu(update)

# Register app handlers
app = Application.builder().token(TELEGRAM_TOKEN).build()
app.add_handler(CommandHandler("start", start_cmd))
app.add_handler(CallbackQueryHandler(add_btn, pattern="^add$"))
app.add_handler(CallbackQueryHandler(remove_btn, pattern="^remove$"))
app.add_handler(CallbackQueryHandler(threshold_btn, pattern="^threshold$"))
app.add_handler(CallbackQueryHandler(rebound_menu_btn, pattern="^rebound_menu$"))
app.add_handler(CallbackQueryHandler(status_btn, pattern="^status$"))
app.add_handler(CallbackQueryHandler(help_btn, pattern="^help$"))
app.add_handler(CallbackQueryHandler(threshold_select, pattern="^threshold_select:.*"))
app.add_handler(CallbackQueryHandler(toggle_select, pattern="^toggle_select:.*"))
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))

threading.Thread(target=monitor_loop, daemon=True).start()
print("Bot running…")
app.run_polling()
