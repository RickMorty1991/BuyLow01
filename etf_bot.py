import yfinance as yf

import sqlite3

import time

import threading

import io

import matplotlib.pyplot as plt



from telegram import Bot, Update, InlineKeyboardButton, InlineKeyboardMarkup, CallbackQuery, ReplyKeyboardMarkup

from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters, CallbackQueryHandler



# ==== SETTINGS ====

TELEGRAM_TOKEN = "8404794616:AAGvQBP2ArgMIzaWDCNSgOwXRQFYBYrx9yA"  # <-- встав свій token

CHAT_ID = 409544912

CHECK_INTERVAL = 600  # 10 хв



# ==== DATABASE INIT ====

    ath = float(df["Close"].max())

    ath_date = df["Close"].idxmax().strftime("%Y-%m-%d")

    return ath, ath_date



def calc_change_percent(now, ago):

    if ago is None or ago == 0:

        return None

    return (now - ago) / ago * 100



def build_chart_bytes(ticker, ath):

    df = yf.Ticker(ticker).history(period="365d")

    hist = df["Close"]

    if hist.empty or ath is None:

        return None

    plt.figure()

    plt.plot(hist)

    plt.axhline(ath)

    plt.title(f"{ticker} | ATH 1Y {ath:.2f} USD")

    buf = io.BytesIO()

    plt.savefig(buf, format="png")

    plt.close()

    buf.seek(0)

    return buf



# ==== BACKGROUND MONITOR ====

def monitor_loop():

    while True:

        items = c.execute("SELECT ticker, threshold, rebound_enabled, last_alerted, rebound_sent FROM subs WHERE chat_id=?", (CHAT_ID,)).fetchall()



        for ticker, threshold, rebound_enabled, last_alerted, rebound_sent in items:

            price_now = get_price_now(ticker)

            price_ago = get_price_1y_ago(ticker)

            ath, ath_date = get_ath_1y(ticker)



            if price_now is None or ath is None:

                continue



            dd = (ath - price_now) / ath * 100

            change = calc_change_percent(price_now, price_ago)



            msg = f"📊 {ticker}\n💰 Ціна зараз: {price_now:.2f} USD\n"

            if price_ago is not None and change is not None:

                arrow = "📈" if change > 0 else "📉"

                msg += f"{arrow} 365d ago: {price_ago:.2f} USD → {price_now:.2f} USD ({change:.2f}%)\n"

            else:

                msg += "📆 365d ago: N/A\n"

            msg += f"📉 Просадка від ATH 1Y: {dd:.2f}%\n📆 ATH 1Y: {ath:.2f} USD ({ath_date})"



            if dd >= threshold and last_alerted == 0:

                chart = build_chart_bytes(ticker, ath)

                if chart:

                    bot.send_photo(CHAT_ID, chart, caption="⚠️ *Просадка від ATH 1Y!*\n\n" + msg, parse_mode="Markdown")

                else:

                    bot.send_message(CHAT_ID, "⚠️ *Просадка від ATH 1Y!*\n\n" + msg, parse_mode="Markdown")



                c.execute("UPDATE subs SET last_alerted=1, rebound_sent=0 WHERE ticker=? AND chat_id=?", (ticker, CHAT_ID))

                conn.commit()



            if dd < threshold and rebound_enabled == 1 and last_alerted == 1 and rebound_sent == 0:

                bot.send_message(CHAT_ID, "📈 *Відновлення ціни після падіння!*\n\n" + msg, parse_mode="Markdown")

                c.execute("UPDATE subs SET rebound_sent=1 WHERE ticker=? AND chat_id=?", (ticker, CHAT_ID))

                conn.commit()



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

        "📘 *Help — опис меню:*\n\n"

        "➕ *Add ETF* — додати ETF у моніторинг\n"

        "📊 *Status* — перевірка всіх ETF, показ ціни зараз, % зміни за 365 днів, DD від ATH 1Y + графіки\n"

        "📉 *Set Threshold* — встановити поріг просадки для 1 ETF\n"

        "📈 *Toggle Rebound* — увімк/вимк алерти відновлення для 1 ETF\n"

        "🗑 *Remove ETF* — видалити ETF зі списку\n\n"

        "📌 *Commands* — список команд"

    )

    await update.message.reply_text(msg, parse_mode="Markdown")



async def list_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):

    await update.message.reply_text("Використовуйте меню або /remove щоб видалити ETF.", parse_mode="Markdown")



async def status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):

    items = c.execute("SELECT ticker, threshold, rebound_enabled, price_ago FROM subs WHERE chat_id=?", (CHAT_ID,)).fetchall()

    if not items:

        return await update.message.reply_text("❗ Немає ETF")



    lines=[]

    for t, th, rb, ago_db in items:

        now=get_price_now(t); ago=get_price_1y_ago(t); ath,ath_date=get_ath_1y(t)

        if now is None: continue

        change=calc_change_percent(now,ago); arrow="📈" if change and change>0 else "📉" if change else ""; chs=f"{arrow} {abs(change):.2f}%" if change else "N/A"

        dd=(ath-now)/ath*100 if ath else None

        lines.append(f"{t}: {now:.2f} USD | Δ365d {chs} | DD {dd:.2f}%")



        if ath:

            chart = build_chart_bytes(t, ath)

            if chart:

                await update.message.reply_photo(chart, caption=f"{t} 365d графік | ATH {ath:.2f} USD ({ath_date})")



    await update.message.reply_text("📊 *Status All ETFs:*\n\n"+"\n".join(lines), parse_mode="Markdown")



async def threshold_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):

    items = [r[0] for r in c.execute("SELECT ticker FROM subs WHERE chat_id=?", (CHAT_ID,)).fetchall()]

    if not items:

        return await update.message.reply_text("❗ Немає ETF")



    buttons=[[InlineKeyboardButton(t,callback_data=f"threshold:{t}")] for t in items]

    await update.message.reply_text("📉 Оберіть ETF:",reply_markup=InlineKeyboardMarkup(buttons))



async def threshold_pick_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):

    q=update.callback_query; await q.answer()

    ticker=q.data.split(":")[1].upper()

    thb=[[InlineKeyboardButton(p,f"threshold_save:{ticker}:{p.replace('%','')}")] for p in ["1%","3%","5%","7%","10%"]]

    await q.message.reply_text("Поріг %:",reply_markup=InlineKeyboardMarkup(thb))



async def threshold_save_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):

    q=update.callback_query; await q.answer()

    _,t,val=q.data.split(":"); ticker=t.upper()

    c.execute("UPDATE subs SET threshold=? WHERE ticker=? AND chat_id=?", (float(val),ticker,CHAT_ID)); conn.commit()

    await q.message.reply_text(f"✔ Поріг {ticker} = {val}%")



async def rebound_toggle_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):

    items = c.execute("SELECT ticker, rebound_enabled FROM subs WHERE chat_id=?", (CHAT_ID,)).fetchall()

    if not items:

        return await update.message.reply_text("❗ Немає ETF")

    buttons=[[InlineKeyboardButton(f"{t} | {'ON' if rb else 'OFF'}",callback_data=f"rebound_toggle:{t}")] for t,rb in items]

    await update.message.reply_text("🔁 Оберіть ETF:",reply_markup=InlineKeyboardMarkup(buttons))



async def rebound_toggle_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):

    q=update.callback_query; await q.answer()

    ticker=q.data.split(":")[1].upper()

    row=c.execute("SELECT rebound_enabled FROM subs WHERE ticker=? AND chat_id=?", (ticker,CHAT_ID)).fetchone()

    new=0 if row and row[0]==1 else 1

    c.execute("UPDATE subs SET rebound_enabled=?, rebound_sent=0 WHERE ticker=? AND chat_id=?", (new,ticker,CHAT_ID)); conn.commit()

    await q.message.reply_text(f"🔁 Rebound {ticker}: {'ON' if new else 'OFF'}")



async def remove_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):

    q=update.callback_query; await q.answer()

    ticker=q.data.split(":")[1].upper()

    c.execute("DELETE FROM subs WHERE ticker=? AND chat_id=?", (ticker,CHAT_ID)); conn.commit()

    await q.message.reply_text(f"✔ {ticker} прибрано зі списку")



async def reply_router(update: Update, context: ContextTypes.DEFAULT_TYPE):

    text=update.message.text.strip()

    if text=="➕ Add ETF": await update.message.reply_text("Введіть ticker:"); context.user_data["action"]="add"; return

    if context.user_data.get("action")=="add": ticker=text.upper(); ago=get_price_1y_ago(ticker) or 0; c.execute("INSERT OR IGNORE INTO subs VALUES(?,?,?,?,?,?)",(ticker,CHAT_ID,5,1,0,0,ago)); conn.commit(); await update.message.reply_text(f"✔ {ticker} додано"); context.user_data["action"]=None; return

    if text=="📊 Status": return await status_cmd(update,context)

    if text=="📉 Set Threshold": return await threshold_menu(update,context)

    if text=="📈 Toggle Rebound": return await rebound_toggle_menu(update,context)

    if text=="🗑 Remove ETF": return await list_cmd(update,context)

    if text=="❓ Help": return await help_cmd(update,context)

    if text=="📌 Commands": return await commands_cmd(update,context)



# ==== REGISTER ====

app=Application.builder().token(TELEGRAM_TOKEN).build()

app.add_handler(CommandHandler("start",start_cmd))

app.add_handler(CommandHandler("list",list_cmd))

app.add_handler(CommandHandler("status",status_cmd))

app.add_handler(CommandHandler("threshold",threshold_menu))

app.add_handler(CommandHandler("rebound",rebound_toggle_menu))

app.add_handler(CommandHandler("help",help_cmd))

app.add_handler(CommandHandler("commands",commands_cmd))

app.add_handler(CallbackQueryHandler(threshold_pick_handler,pattern="^threshold:"))

app.add_handler(CallbackQueryHandler(threshold_save_handler,pattern="^threshold_save:"))

app.add_handler(CallbackQueryHandler(rebound_toggle_handler,pattern="^rebound_toggle:"))

app.add_handler(CallbackQueryHandler(remove_handler,pattern="^remove:"))

app.add_handler(CallbackQueryHandler(noop,pattern="^noop"))

app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND,reply_router))



threading.Thread(target=monitor_loop,daemon=True).start()

print("Bot running…")

app.run_polling()
