import yfinance as yf
import sqlite3
import time
import threading
import io
import matplotlib.pyplot as plt

from telegram import Bot, Update, InlineKeyboardButton, InlineKeyboardMarkup, CallbackQuery, ReplyKeyboardMarkup
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters, CallbackQueryHandler

TELEGRAM_TOKEN = "8404794616:AAGvQBP2ArgMIzaWDCNSgOwXRQFYBYrx9yA"  # <-- заміни на свій token
CHAT_ID = 409544912
CHECK_INTERVAL = 600

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
ath = float(df["Close"].max())
ath_date = df["Close"].idxmax().strftime("%Y-%m-%d")
return ath, ath_date

def build_chart_bytes(ticker, ath):
df = yf.Ticker(ticker).history(period="365d")
hist = df["Close"]
if hist.empty:
return None
plt.figure()
plt.plot(hist)
plt.axhline(ath)
buf = io.BytesIO()
plt.savefig(buf, format="png")
plt.close()
buf.seek(0)
return buf

def monitor_loop():
while True:
items = c.execute("SELECT ticker, threshold, rebound_enabled, last_alerted, rebound_sent FROM subs WHERE chat_id=?", (CHAT_ID,)).fetchall()
for ticker, threshold, rebound_enabled, last_alerted, rebound_sent in items:
now = get_price_now(ticker)
ago = get_price_1y_ago(ticker)
ath, ath_date = get_ath_1y(ticker)
if now is None or ath is None:
continue
dd = (ath - now) / ath * 100
change = ((now - ago) / ago * 100) if ago else None
msg = f"📊 {ticker}\n💰 Ціна: {now:.2f} USD\nΔ365d: {change:.2f}%\nDD ATH1Y: {dd:.2f}%\nATH1Y: {ath:.2f} USD ({ath_date})"
if dd >= threshold and last_alerted == 0:
chart = build_chart_bytes(ticker, ath)
if chart:
bot.send_photo(CHAT_ID, chart, caption="⚠️ " + msg, parse_mode="Markdown")
else:
bot.send_message(CHAT_ID, "⚠️ " + msg, parse_mode="Markdown")
c.execute("UPDATE subs SET last_alerted=1, rebound_sent=0 WHERE ticker=? AND chat_id=?", (ticker, CHAT_ID))
conn.commit()
if dd < threshold and rebound_enabled == 1 and last_alerted == 1 and rebound_sent == 0:
bot.send_message(CHAT_ID, "📈 *Rebound*\n\n" + msg, parse_mode="Markdown")
c.execute("UPDATE subs SET rebound_sent=1 WHERE ticker=? AND chat_id=?", (ticker, CHAT_ID))
conn.commit()
time.sleep(CHECK_INTERVAL)

async def start_cmd(update, context):
kb = [["➕ Add ETF", "📊 Status"], ["📉 Set Threshold", "📈 Toggle Rebound"], ["❓ Help", "📌 Commands"]]
await update.message.reply_text("Menu:", reply_markup=ReplyKeyboardMarkup(kb, resize_keyboard=True))

async def list_cmd(update, context):
items = c.execute("SELECT ticker, threshold, rebound_enabled FROM subs WHERE chat_id=?", (CHAT_ID,)).fetchall()
if not items:
return await update.message.reply_text("No ETFs")
buttons = []
for t, th, rb in items:
buttons.append([InlineKeyboardButton(f"{t} | Remove", callback_data=f"remove:{t}")])
await update.message.reply_text("📌 My ETFs:", reply_markup=InlineKeyboardMarkup(buttons))

async def remove_callback(update, context):
q = update.callback_query
await q.answer()
ticker = q.data.split(":")[1].upper()
c.execute("DELETE FROM subs WHERE ticker=? AND chat_id=?", (ticker, CHAT_ID))
conn.commit()
await q.message.reply_text(f"Removed {ticker}")
await list_cmd(update, context)

async def status_reply(update, context):
await update.message.reply_text("Checking…")
items = [r[0] for r in c.execute("SELECT ticker FROM subs WHERE chat_id=?", (CHAT_ID,)).fetchall()]
for t in items:
ath, _ = get_ath_1y(t)
chart = build_chart_bytes(t, ath)
if chart:
await update.message.reply_photo(chart, caption=f"{t} chart")

async def rebound_toggle_menu(update, context):
await update.message.reply_text("Use /rebound for toggle")

app = Application.builder().token(TELEGRAM_TOKEN).build()
app.add_handler(CommandHandler("start", start_cmd))
app.add_handler(CommandHandler("list", list_cmd))
app.add_handler(CommandHandler("status", status_reply))
app.add_handler(CallbackQueryHandler(remove_callback, pattern="^remove:"))
app.add_handler(CallbackQueryHandler(remove_callback, pattern="^rebound_toggle:"))
app.add_handler(CallbackQueryHandler(remove_callback, pattern="^threshold:"))
app.add_handler(CallbackQueryHandler(noop, pattern="^noop"))
app.add_handler(CallbackQueryHandler(remove_callback, pattern="^remove:"))
app.add_handler(CallbackQueryHandler(noop, pattern="^noop"))
app.add_handler(CallbackQueryHandler(remove_callback, pattern="^remove:"))
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, reply_router))

threading.Thread(target=monitor_loop, daemon=True).start()
print("Bot running…")
app.run_polling()
