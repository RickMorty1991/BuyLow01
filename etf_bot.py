import yfinance as yf
import sqlite3
import time
import threading
import io
import matplotlib.pyplot as plt
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters, CallbackQueryHandler

TOKEN = "8404794616:AAHUJeJp_wvOa8poUXcZufJRXXC72pZZgU0"
INTERVAL = 600  # 10 хв

# --- Database ---
    if df.empty:
        return None
    plt.figure()
    plt.plot(df.Close)
    plt.axhline(top)
    plt.title(f"{ticker.upper()} | TOP 365d: {top:.2f} USD")
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
            ago = get_price_1y_ago(t)
            if now is None:
                continue

            if top == 0:
                new_top, d = get_top_365(t)
                if not new_top:
                    continue
                c.execute("UPDATE subs SET top=?, top_date=? WHERE ticker=? AND chat_id=?", (new_top, d, t, chat_id))
                conn.commit()
                top, top_date = new_top, d

            dd = (top - now) / top * 100
            yc = calc_yearly_change(now, ago)
            yc_str = f"{yc:.2f}%" if yc is not None else "N/A"

            text_msg = (
                f"📉 *{t.upper()} Update*\n"
                f"Ціна зараз: `{now:.2f} USD`\n"
                f"Δ1Y: `{yc_str}`\n"
                f"Просадка від TOP 365d: `{dd:.2f}%`\n"
                f"Поріг: `{th}%` | Rebound: `{'ON' if rb else 'OFF'}`"
            )

            if dd >= th and last == 0:
                chart = build_chart(t, top)
                if chart:
                    bot.send_photo(chat_id=chat_id, photo=chart, caption="⚠️ ALERT\n\n" + text_msg, parse_mode="Markdown")
                else:
                    bot.send_message(chat_id=chat_id, text="⚠️ ALERT\n\n" + text_msg, parse_mode="Markdown")
                c.execute("UPDATE subs SET last_alert=1, rebound_sent=0 WHERE ticker=? AND chat_id=?", (t, chat_id))
                conn.commit()

            if dd < th and rb == 1 and last == 1 and rbs == 0:
                bot.send_message(chat_id=chat_id, text="📈 REBOUND\n\n" + text_msg, parse_mode="Markdown")
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
        ["❓ Help"]
    ], resize_keyboard=True)
    await update.message.reply_text("Вітаю! Обирайте команду 👇", reply_markup=menu)

async def help_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "ℹ *Help — доступні команди*\n\n"
        "/start — відкрити меню\n"
        "/add SPY — додати ETF у моніторинг\n"
        "/threshold SPY — встановити поріг просадки\n"
        "/rebound SPY — увімк/вимк сповіщення rebound\n"
        "/list — список підписок\n"
        "/status — статус і графіки\n\n"
        "🧩 *Меню кнопок:*\n"
        "➕ Add ETF — режим додавання ETF\n"
        "📌 My ETFs — список ETF\n"
        "📉 Set Threshold — обрати ETF для порогу\n"
        "📈 Toggle Rebound — перемкнути rebound для ETF\n"
        "🔁 Force Check All — перевірити все негайно\n"
        "📊 Status — ринкові дані\n"
        "❓ Help — допомога"
    , parse_mode="Markdown")

async def list_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    rows = c.execute("SELECT ticker, threshold, rebound FROM subs WHERE chat_id=?", (update.message.chat_id,)).fetchall()
    if not rows:
        return await update.message.reply_text("📭 Немає ETF. Додайте через /add SPY")
    msg = "📌 *Ваші ETF:*\n\n" + "\n".join([f"{t.upper()} → поріг {th}% | Rebound: {'ON' if rb else 'OFF'}" for t, th, rb in rows])
    await update.message.reply_text(msg, parse_mode="Markdown")

async def add_etf_btn(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Введіть тікер через /add SPY")

async def threshold_btn(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    rows = c.execute("SELECT ticker FROM subs WHERE chat_id=?", (update.message.chat_id,)).fetchall()
    if not rows:
        return await update.message.reply_text("📭 Немає ETF")
    btns = [[InlineKeyboardButton(r[0].upper(), callback_data=f"threshold:{r[0].upper()}")] for r in rows]
    await update.message.reply_text("Оберіть ETF:", reply_markup=InlineKeyboardMarkup(btns))

async def threshold_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    ticker = q.data.split(":")[1]
    btns = [[InlineKeyboardButton(x, callback_data=f"threshold_set:{ticker}:{x.strip('%')}")] for x in ["1%","3%","5%","7%","10%"]]
    await q.message.reply_text("Встановіть поріг:", reply_markup=InlineKeyboardMarkup(btns))

async def threshold_set_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    _, ticker, val = q.data.split(":")
    val = float(val)
    c.execute("UPDATE subs SET threshold=? WHERE ticker=? AND chat_id=?", (val, ticker, update.callback_query.message.chat_id))
    conn.commit()
    await q.message.reply_text(f"🔧 Поріг {ticker} = {val}%")

async def rebound_toggle_btn(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    rows = c.execute("SELECT ticker FROM subs WHERE chat_id=?", (update.message.chat_id,)).fetchall()
    if not rows:
        return await update.message.reply_text("📭 Немає ETF")
    btns = [[InlineKeyboardButton(r[0].upper(), callback_data=f"rebound:{r[0].upper()}")] for r in rows]
    await update.message.reply_text("Оберіть ETF:", reply_markup=InlineKeyboardMarkup(btns))

async def rebound_toggle_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    ticker = q.data.split(":")[1].upper()
    row = c.execute("SELECT rebound FROM subs WHERE ticker=? AND chat_id=?", (ticker, update.callback_query.message.chat_id)).fetchone()
    if row:
        new = 0 if row[0] == 1 else 1
        c.execute("UPDATE subs SET rebound=?, rebound_sent=0 WHERE ticker=? AND chat_id=?", (new, ticker, update.message.chat_id))
        conn.commit()
        await q.message.reply_text(f"🔁 Rebound {ticker}: {'ON' if new else 'OFF'}")

async def status_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat_id = update.message.chat_id
    rows = c.execute("SELECT ticker, threshold, rebound, top, top_date FROM subs WHERE chat_id=?", (chat_id,)).fetchall()
    if not rows:
        return await update.message.reply_text("📭 Немає ETF")

    for t, th, rb, top, d in rows:
        now = get_price(t)
        ago = get_price_1y_ago(t)
        if now:
            yc = calc_yearly_change(now, ago)
            yc_str = f"{yc:.2f}%" if yc else "N/A"
            dd = (top - now) / top * 100 if top else None
            chart = build_chart(t, top)
            if chart:
                await ctx.bot.send_photo(chat_id=chat_id, photo=chart, caption=f"{t.upper()} графік")
            await update.message.reply_text(
                f"{t.upper()}: {now:.2f} USD | Δ1Y {yc_str} | DD {dd:.2f}% | TOP365 {top:.2f} ({d})"
            )
        else:
            await update.message.reply_text(f"{t.upper()}: немає даних")

async def reply_router(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip().upper()
    if text.startswith("ADD ETF"):
        return await add_etf_btn(update, ctx)
    if text.startswith("MY ETFS"):
        return await list_cmd(update, ctx)
    if text.startswith("SET THRESHOLD"):
        return await threshold_btn(update, ctx)
    if text.startswith("TOGGLE REBOUND"):
        rows = c.execute("SELECT ticker FROM subs WHERE chat_id=?", (update.message.chat_id,)).fetchall()
        if rows:
            btns = [[InlineKeyboardButton(r[0].upper(), callback_data=f"rebound:{r[0].upper()}")] for r in rows]
            return await update.message.reply_text("Оберіть ETF:", reply_markup=InlineKeyboardMarkup(btns))
        return await update.message.reply_text("📭 Немає ETF")
    if text.startswith("FORCE CHECK ALL") or text.startswith("STATUS"):
        return await status_cmd(update, ctx)
    if text.startswith("HELP"):
        return await help_cmd(update, ctx)
    await update.message.reply_text("❗ Невідома команда. /help")

# --- Run ---
app = Application.builder().token(TOKEN).build()
bot = app.bot

app.add_handler(CommandHandler("start", start))
app.add_handler(CommandHandler("help", help_cmd))
app.add_handler(CommandHandler("commands", commands_cmd))
app.add_handler(CommandHandler("list", list_cmd))
app.add_handler(CommandHandler("add", add_cmd))
app.add_handler(CommandHandler("threshold", threshold_btn))
app.add_handler(CommandHandler("rebound", rebound_toggle_btn))
app.add_handler(CommandHandler("status", status_cmd))
app.add_handler(CallbackQueryHandler(threshold_menu, pattern="^threshold:"))
app.add_handler(CallbackQueryHandler(rebound_toggle_handler, pattern="^rebound:"))
app.add_handler(CallbackQueryHandler(threshold_set_handler, pattern="^threshold_set:"))
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, reply_router))

threading.Thread(target=monitor_loop, args=(bot,), daemon=True).start()

print("Bot running…")
app.run_polling()
