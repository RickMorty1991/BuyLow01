import yfinance as yf
import sqlite3
import time
import threading
import io
import matplotlib.pyplot as plt

from telegram import Bot, Update, ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardButton, InlineKeyboardMarkup, CallbackQuery
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters, CallbackQueryHandler

# ==== SETTINGS ====
TELEGRAM_TOKEN = "8404794616:AAED6s15B7E0_B58x5iucCPtxsL139JOh9w"  # <-- Встав новий token
                f"📆 365d ago: {price_ago:.2f} USD ({change:.2f}%)\n" if price_ago else
                f"📊 {ticker}\n💰 Ціна зараз: {price_now:.2f} USD\nΔ365d: N/A\n"
            )
            msg += f"📉 DD від ATH 1Y: {dd:.2f}%\n📆 ATH 1Y: {ath:.2f} USD ({ath_date})"

            # Alert падіння
            if dd >= threshold and last_alerted == 0:
                chart = build_chart_bytes(ticker, ath)
                if chart:
                    bot.send_photo(CHAT_ID, chart, caption="⚠️ *Просадка від ATH!*\n\n" + msg, parse_mode="Markdown")
                else:
                    bot.send_message(CHAT_ID, "⚠️ *Просадка від ATH!*\n\n" + msg, parse_mode="Markdown")

                c.execute("UPDATE subs SET last_alerted=1, rebound_sent=0 WHERE ticker=? AND chat_id=?", (ticker, CHAT_ID))
                conn.commit()

            # Rebound alert
            if dd < threshold and rebound_enabled == 1 and last_alerted == 1 and rebound_sent == 0:
                bot.send_message(CHAT_ID, "📈 *Відновлення ціни!*\n\n" + msg, parse_mode="Markdown")
                c.execute("UPDATE subs SET rebound_sent=1 WHERE ticker=? AND chat_id=?", (ticker, CHAT_ID))
                conn.commit()

            # Reset rebound_sent
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
    await update.message.reply_text("Вітаю! Оберіть команду з меню 👇", reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True))

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = (
        "🤖 *ETF Monitor Bot — короткий опис команд:*\n\n"
        "➕ *Add ETF* — додати ETF у відстеження\n"
        "📌 *My ETFs* — список ваших ETF\n"
        "📉 *Set Threshold* — встановити поріг просадки від ATH 1Y\n"
        "📈 *Toggle Rebound* — увімк/вимк алерти відновлення\n"
        "📊 *Status* — поточна ціна + % зміна за 365d + DD від ATH 1Y + графік\n"
        "🗑 *Remove ETF* — видалити ETF зі списку\n"
        "🔁 *Force Check All* — перевірити все негайно\n\n"
        "Текстові команди:\n"
        "/start, /list, /status, /threshold, /rebound, /remove, /help, /commands"
    )
    await update.message.reply_text(msg, parse_mode="Markdown")

async def list_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    items = c.execute("SELECT ticker, threshold, rebound_enabled FROM subs WHERE chat_id=?", (CHAT_ID,)).fetchall()
    if not items:
        return await update.message.reply_text("❗ Немає ETF")

    buttons = [[InlineKeyboardButton(f"{t} | 🗑 Remove", callback_data=f"remove:{t}")] for t,_,_ in items]
    lines = "\n".join([f"{t} (поріг {th}%) | Rebound {'ON' if rb else 'OFF'}" for t,th,rb in items])

    await update.message.reply_text("📌 *Ваші ETF:*\n\n"+lines, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(buttons))

async def status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    items = [r[0] for r in c.execute("SELECT ticker FROM subs WHERE chat_id=?", (CHAT_ID,)).fetchall()]
    if not items:
        return await update.message.reply_text("❗ Немає ETF")

    lines=[]
    for t in items:
        now = get_price_now(t)
        ago = get_price_1y_ago(t)
        ath, ath_date = get_ath_1y(t)
        if now is None or ath is None:
            continue
        change = calc_change_percent(now, ago)
        direction = "виріс" if change and change>0 else "впав" if change else ""
        change_str = f"{direction} {abs(change):.2f}%" if change else "N/A"
        lines.append(f"{t}: {now:.2f} USD | Δ365d {change_str}")

        chart = build_chart_bytes(t, ath)
        if chart:
            await update.message.reply_photo(chart, caption=f"📊 {t} графік 365d | ATH {ath:.2f} USD ({ath_date})")

    await update.message.reply_text("📊 *Поточний статус:*\n\n"+"\n".join(lines), parse_mode="Markdown")

async def threshold_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    items = [r[0] for r in c.execute("SELECT ticker FROM subs WHERE chat_id=?", (CHAT_ID,)).fetchall()]
    if not items:
        return await update.message.reply_text("❗ Немає ETF")
    btn = [[InlineKeyboardButton(t, callback_data=f"threshold_pick:{t}")] for t in items]
    await update.message.reply_text("Оберіть ETF:", reply_markup=InlineKeyboardMarkup(btn))

async def threshold_pick_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q=update.callback_query; await q.answer()
    ticker=q.data.split(":")[1].upper()
    thb=[[InlineKeyboardButton("1%",f"threshold_save:{ticker}:1")],[InlineKeyboardButton("3%",f"threshold_save:{ticker}:3")],[InlineKeyboardButton("5%",f"threshold_save:{ticker}:5")],[InlineKeyboardButton("7%",f"threshold_save:{ticker}:7")],[InlineKeyboardButton("10%",f"threshold_save:{ticker}:10")]]
    await q.message.reply_text("Поріг %:", reply_markup=InlineKeyboardMarkup(thb))

async def threshold_save(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q=update.callback_query; await q.answer()
    _, t, val = q.data.split(":"); ticker=t.upper()
    c.execute("UPDATE subs SET threshold=? WHERE ticker=? AND chat_id=?", (float(val), ticker, CHAT_ID))
    conn.commit()
    await q.message.reply_text(f"✔ Поріг для {ticker} = {val}%")

async def rebound_toggle_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    items=c.execute("SELECT ticker,rebound_enabled FROM subs WHERE chat_id=?", (CHAT_ID,)).fetchall()
    if not items: return await update.message.reply_text("❗ Немає ETF")
    btn=[[InlineKeyboardButton(f"{t} | {'ON' if rb else 'OFF'}", callback_data=f"rebound:{t}")] for t,rb in items]
    await update.message.reply_text("🔁 Rebound:", reply_markup=InlineKeyboardMarkup(btn))

async def rebound_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q=update.callback_query; await q.answer()
    ticker=q.data.split(":")[1].upper()
    row=c.execute("SELECT rebound_enabled FROM subs WHERE ticker=? AND chat_id=?", (ticker,CHAT_ID)).fetchone()
    new=0 if row and row[0]==1 else 1
    c.execute("UPDATE subs SET rebound_enabled=?, rebound_sent=0 WHERE ticker=? AND chat_id=?", (new,ticker,CHAT_ID))
    conn.commit()
    await q.message.reply_text(f"🔁 Rebound {ticker}: {'ON' if new else 'OFF'}")

async def remove_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    items=[r[0] for r in c.execute("SELECT ticker FROM subs WHERE chat_id=?", (CHAT_ID,)).fetchall()]
    if not items: return await update.message.reply_text("❗ Немає ETF")
    btn=[[InlineKeyboardButton(f"🗑 {t}", callback_data=f"remove:{t}")] for t in items]
    await update.message.reply_text("Видалити ETF:", reply_markup=InlineKeyboardMarkup(btn))

async def remove_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q=update.callback_query; await q.answer()
    ticker=q.data.split(":")[1].upper()
    c.execute("DELETE FROM subs WHERE ticker=? AND chat_id=?", (ticker,CHAT_ID)); conn.commit()
    await q.message.reply_text(f"🗑 {ticker} видалено зі списку")

# ==== REGISTER ====
application = Application.builder().token(TELEGRAM_TOKEN).build()

application.add_handler(CommandHandler("start", start_cmd))
application.add_handler(CommandHandler("list", list_cmd))
application.add_handler(CommandHandler("status", status_cmd))
application.add_handler(CommandHandler("threshold", threshold_cmd))
application.add_handler(CommandHandler("rebound", rebound_toggle_cmd))
application.add_handler(CommandHandler("remove", remove_cmd))
application.add_handler(CommandHandler("help", help_cmd))
application.add_handler(CommandHandler("commands", commands_cmd))

application.add_handler(CallbackQueryHandler(threshold_pick_handler, pattern="^threshold_pick:"))
application.add_handler(CallbackQueryHandler(rebound_handler, pattern="^rebound:"))
application.add_handler(CallbackQueryHandler(remove_handler, pattern="^remove:"))

application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, reply_router))

threading.Thread(target=monitor_loop, daemon=True).start()
print("Bot running…")
application.run_polling()
