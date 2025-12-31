import yfinance as yf
import sqlite3
import time
import threading
import io
import matplotlib.pyplot as plt

from telegram import (
    Bot,
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
)
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    CallbackQueryHandler,
    filters,
)

# ==== SETTINGS ====
TELEGRAM_TOKEN = "8404794616:AAGvQBP2ArgMIzaWDCNSgOwXRQFYBYrx9yA"


# ==== MONITOR LOOP ====
def monitor_loop():
    bot = Bot(token=TELEGRAM_TOKEN)

    while True:
        rows = c.execute(
            "SELECT ticker, threshold, rebound_enabled, last_alerted, rebound_sent "
            "FROM subs WHERE chat_id=?",
            (CHAT_ID,),
        ).fetchall()

        for ticker, threshold, rebound_enabled, last_alerted, rebound_sent in rows:
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
                msg += (
                    f"{arrow} 365d ago: {ago:.2f} USD → "
                    f"{now:.2f} USD ({change:.2f}%)\n"
                )
            else:
                msg += "📆 365d ago: N/A\n"

            msg += (
                f"📉 Просадка від ATH 1Y: {dd:.2f}%\n"
                f"📆 ATH 1Y: {ath:.2f} USD ({ath_date})"
            )

            # Drop alert
            if dd >= threshold and last_alerted == 0:
                chart = build_chart_bytes(ticker, ath)
                if chart:
                    bot.send_photo(CHAT_ID, chart, caption="⚠️ Просадка!\n\n" + msg)
                else:
                    bot.send_message(CHAT_ID, "⚠️ Просадка!\n\n" + msg)

                c.execute(
                    "UPDATE subs SET last_alerted=1, rebound_sent=0 "
                    "WHERE ticker=? AND chat_id=?",
                    (ticker, CHAT_ID),
                )
                conn.commit()

            # Rebound
            if (
                dd < threshold
                and rebound_enabled == 1
                and last_alerted == 1
                and rebound_sent == 0
            ):
                bot.send_message(CHAT_ID, "📈 Відновлення!\n\n" + msg)
                c.execute(
                    "UPDATE subs SET rebound_sent=1 "
                    "WHERE ticker=? AND chat_id=?",
                    (ticker, CHAT_ID),
                )
                conn.commit()

            # Reset rebound if falls again
            if dd >= threshold and rebound_sent == 1:
                c.execute(
                    "UPDATE subs SET rebound_sent=0 "
                    "WHERE ticker=? AND chat_id=?",
                    (ticker, CHAT_ID),
                )
                conn.commit()

        time.sleep(CHECK_INTERVAL)


# ==== BOT HANDLERS ====
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        ["➕ Add ETF", "📊 Status"],
        ["📉 Set Threshold", "📈 Toggle Rebound"],
        ["🗑 Remove ETF", "❓ Help", "📌 Commands"],
    ]
    await update.message.reply_text(
        "Вітаю! Оберіть команду 👇",
        reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True),
    )


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = (
        "📘 *Help — опис опцій меню:*\n\n"
        "➕ *Add ETF* — додати ETF\n"
        "📊 *Status* — статус ETF\n"
        "📉 *Set Threshold* — поріг просадки\n"
        "📈 *Toggle Rebound* — відновлення\n"
        "🗑 *Remove ETF* — видалити ETF\n"
    )
    await update.message.reply_text(msg, parse_mode="Markdown")


async def commands_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = (
        "/start\n"
        "/list\n"
        "/status\n"
        "/threshold\n"
        "/rebound\n"
        "/remove\n"
        "/help\n"
        "/commands"
    )
    await update.message.reply_text(msg)


# ==== REGISTER APP ====
app = Application.builder().token(TELEGRAM_TOKEN).build()

app.add_handler(CommandHandler("start", start_cmd))
app.add_handler(CommandHandler("help", help_cmd))
app.add_handler(CommandHandler("commands", commands_cmd))
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, reply_router))
app.add_handler(CallbackQueryHandler(remove_handler, pattern="^remove:"))

threading.Thread(target=monitor_loop, daemon=True).start()

print("Bot running…")
app.run_polling()

