import yfinance as yf
import sqlite3
import time
import threading
import io
import matplotlib.pyplot as plt

from telegram import Bot, Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters, CallbackQueryHandler

# ==== SETTINGS ====
TELEGRAM_TOKEN = "8404794616:AAHUJeJp_wvOa8poUXcZufJRXXC72pZZgU0"  # ✔ твій робочий токен уже вставлено
            now = get_price_now(ticker)
            ago = get_price_1y_ago(ticker)
            ath, ath_date = get_ath_1y(ticker)

            if now is None or ath is None:
                continue

            dd = (ath - now) / ath * 100
            change = calc_change_percent(now, ago)

            msg = f"📊 {ticker}\n💰 Ціна зараз: {now:.2f} USD\n"
            if ago is not None and change is not None:
                arrow = "📈" if change > 0 else "📉"
                msg += f"{arrow} 365d ago: {ago:.2f} USD → {now:.2f} USD ({change:.2f}%)\n"
            else:
                msg += "📆 365d ago: N/A\n"
            msg += f"📉 Просадка від ATH 1Y: {dd:.2f}%\n📆 ATH 1Y: {ath:.2f} USD ({ath_date})"

            # Падіння нижче порогу
            if dd >= threshold and last_alerted == 0:
                chart = build_chart_bytes(ticker, ath)
                if chart:
                    bot.send_photo(CHAT_ID, chart, caption="⚠️ Просадка!\n\n" + msg)
                else:
                    bot.send_message(CHAT_ID, "⚠️ Просадка!\n\n" + msg)

                c.execute("UPDATE subs SET last_alerted=1, rebound_sent=0 WHERE ticker=? AND chat_id=?", (ticker, CHAT_ID))
                conn.commit()

            # Відновлення (Rebound)
            if dd < threshold and rebound_enabled == 1 and last_alerted == 1 and rebound_sent == 0:
                bot.send_message(CHAT_ID, "📈 Відновлення!\n\n" + msg)
                c.execute("UPDATE subs SET rebound_sent=1 WHERE ticker=? AND chat_id=?", (ticker, CHAT_ID))
                conn.commit()

            # Скидання rebound flag, якщо знову впав
            if dd >= threshold and rebound_sent == 1:
                c.execute("UPDATE subs SET rebound_sent=0 WHERE ticker=? AND chat_id=?", (ticker, CHAT_ID))
                conn.commit()

        time.sleep(CHECK_INTERVAL)

# ==== BOT HANDLERS ====
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        ["➕ Add ETF", "📊 Status"],
        ["📉 Set Threshold", "📈 Toggle Rebound"],
        ["🗑 Remove ETF", "❓ Help", "📌 Commands"]
    ]
    await update.message.reply_text("Вітаю! Оберіть команду 👇", reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True))

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = (
        "📘 *Help — опис опцій меню:*\n\n"
        "➕ *Add ETF* — додати ETF у моніторинг\n"
        "📌 *My ETFs* — переглянути список ETF\n"
        "📉 *Set Threshold* — встановити поріг просадки для 1 ETF\n"
        "📈 *Toggle Rebound* — увімк/вимк відновлення для 1 ETF\n"
        "📊 *Status* — перевірити всі ETF, вивести ціни та графіки\n"
        "🗑 *Remove ETF* — видалити ETF зі списку\n\n"
    )
    await update.message.reply_text(msg, parse_mode="Markdown")

async def commands_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = (
        "📌 *Список команд:*\n\n"
        "/start — відкрити меню\n"
        "/list — список ETF\n"
        "/status — статус ETF\n"
        "/threshold — поріг просадки\n"
        "/rebound — Toggle Rebound\n"
        "/remove — видалити ETF\n"
        "/help — Help menu\n"
        "/commands — список команд"
    )
    await update.message.reply_text(msg, parse_mode="Markdown")

async def list_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    items = c.execute("SELECT ticker FROM subs WHERE chat_id=?", (CHAT_ID,)).fetchall()
    if not items:
        return await update.message.reply_text("❗ Немає ETF")

    buttons = []
    for (t,) in items:
        buttons.append([InlineKeyboardButton(f"{t} | 🗑 Remove", callback_data=f"remove:{t}")])

    await update.message.reply_text("📌 *My ETFs:* 👇", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(buttons))

async def status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    items = c.execute("SELECT ticker FROM subs WHERE chat_id=?", (CHAT_ID,)).fetchall()
    if not items:
        return await update.message.reply_text("❗ Немає ETF")

    for (t,) in items:
        now = get_price_now(t)
        ath, ath_date = get_ath_1y(t)
        ago = get_price_1y_ago(t)
        change = calc_change_percent(now, ago) if now and ago else None
        dd = (ath - now) / ath * 100 if ath and now else None

        text = f"{t}\n💰 Ціна зараз: {now:.2f} USD\n📆 ATH 1Y: {ath:.2f} USD ({ath_date})\n"
        if change is not None:
            arrow = "📈" if change > 0 else "📉"
            text += f"{arrow} Δ365d: {change:.2f}%\n"
        else:
            text += "Δ365d: N/A\n"
        if dd is not None:
            text += f"📉 DD від ATH 1Y: {dd:.2f}%\n"

        chart = build_chart_bytes(t, ath)
        if chart:
            await update.message.reply_photo(chart, caption=text)
        else:
            await update.message.reply_text(text)

    await update.message.reply_text("📊 Status перевірено ✔")

async def threshold_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    items = c.execute("SELECT ticker FROM subs WHERE chat_id=?", (CHAT_ID,)).fetchall()
    if not items:
        return await update.message.reply_text("❗ Немає ETF")

    buttons = [[InlineKeyboardButton(t[0], callback_data=f"threshold_pick:{t[0]}")] for t in items]
    await update.message.reply_text("📉 Оберіть ETF:", reply_markup=InlineKeyboardMarkup(buttons))

async def threshold_pick_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q: CallbackQuery = update.callback_query
    await q.answer()
    ticker = q.data.split(":")[1].upper()
    buttons = [[InlineKeyboardButton(p, callback_data=f"threshold_save:{ticker}:{p.replace('%','')}")] for p in ["1%","3%","5%","7%","10%"]]
    await q.message.reply_text("Поріг %:", reply_markup=InlineKeyboardMarkup(buttons))

async def threshold_save_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    _, ticker, val = q.data.split(":")
    ticker = ticker.upper()
    c.execute("UPDATE subs SET threshold=? WHERE ticker=? AND chat_id=?", (float(val), ticker, CHAT_ID))
    conn.commit()
    await q.message.reply_text(f"✔ Поріг {ticker} = {val}%")

async def remove_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    ticker = q.data.split(":")[1].upper()
    c.execute("DELETE FROM subs WHERE ticker=? AND chat_id=?", (ticker, CHAT_ID))
    conn.commit()
    await q.message.reply_text(f"🗑 {ticker} видалено зі списку")
    await list_cmd(update, context)

async def rebound_toggle_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    items = c.execute("SELECT ticker, rebound_enabled FROM subs WHERE chat_id=?", (CHAT_ID,)).fetchall()
    if not items:
        return await update.message.reply_text("❗ Немає ETF")
    buttons = [[InlineKeyboardButton(f"{t} | {'ON' if rb else 'OFF'}", callback_data=f"rebound_toggle:{t}")] for t, rb in items]
    await update.message.reply_text("🔁 Оберіть ETF:", reply_markup=InlineKeyboardMarkup(buttons))

async def rebound_toggle_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    ticker = q.data.split(":")[1].upper()
    row = c.execute("SELECT rebound_enabled FROM subs WHERE ticker=? AND chat_id=?", (ticker, CHAT_ID)).fetchone()
    new_state = 0 if row and row[0] == 1 else 1
    c.execute("UPDATE subs SET rebound_enabled=?, rebound_sent=0 WHERE ticker=? AND chat_id=?", (new_state, ticker, CHAT_ID))
    conn.commit()
    await q.message.reply_text(f"🔁 Rebound {ticker}: {'ON' if new_state else 'OFF'}")
    await list_cmd(update, context)

async def reply_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if text == "➕ Add ETF":
        await update.message.reply_text("Введіть ticker:"); context.user_data["action"]="add"; return
    if context.user_data.get("action")=="add":
        ticker=text.upper(); now=get_price_now(ticker); ath,_=get_ath_1y(ticker); ago=get_price_1y_ago(ticker) or 0
        if now and ath:
            dd=(ath-now)/ath*100
        else:
            dd=0
        c.execute("INSERT OR IGNORE INTO subs(ticker,chat_id,threshold,rebound_enabled,last_alerted,rebound_sent,price_ago) VALUES(?,?,?,?,?,?,?)",(ticker,CHAT_ID,5,1,0,0,ago))
        conn.commit(); await update.message.reply_text(f"✔ {ticker} додано"); context.user_data["action"]=None; return
    if text=="📊 Status": return await status_cmd(update,context)
    if text=="📉 Set Threshold": return await threshold_menu(update,context)
    if text=="📈 Toggle Rebound": return await rebound_toggle_menu(update,context)
    if text=="🗑 Remove ETF": return await remove_cmd(update,context)
    if text=="📌 My ETFs": return await list_cmd(update,context)
    if text=="❓ Help": return await help_cmd(update,context)
    if text=="📌 Commands": return await commands_cmd(update,context)

# ==== REGISTER APP ====
app = Application.builder().token(TELEGRAM_TOKEN).build()
app.add_handler(CommandHandler("start", start_cmd))
app.add_handler(CommandHandler("list", list_cmd))
app.add_handler(CommandHandler("status", status_cmd))
app.add_handler(CommandHandler("threshold", threshold_menu))
app.add_handler(CommandHandler("rebound", rebound_toggle_menu))
app.add_handler(CommandHandler("help", help_cmd))
app.add_handler(CommandHandler("commands", commands_cmd))
app.add_handler(CallbackQueryHandler(remove_handler, pattern="^remove:"))
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, reply_router))

threading.Thread(target=monitor_loop, daemon=True).start()
print("Bot running…")
app.run_polling()
