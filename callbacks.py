from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from price_helpers import get_price_now, get_ath_52w, calc_change, build_chart_bytes

from db import remove_sub, update_threshold, toggle_rebound, get_subs



async def callbacks(update, context):

    q = update.callback_query

    await q.answer()

    chat_id = q.message.chat.id

    data = q.data



    # SHOW STATUS

    if data.startswith("status:"):

        ticker = data.split(":")[1].upper()

        now = get_price_now(ticker)

        ath, ath_date = get_ath_52w(ticker)

        if now is None or ath is None:

            return await q.message.reply_text("❗ No data")



        dd = (ath - now) / ath * 100

        change = calc_change(now, get_price_now(ticker))  # simple 365 fallback demo



        msg = (

            f"📊 *{ticker}*\n"

            f"💰 {now:.2f} USD\n"

            f"📆 ATH {ath:.2f} ({ath_date})\n"

            f"📉 Drawdown {dd:.2f}%"

        )

        chart = build_chart_bytes(ticker, ath)

        if chart:

            await q.message.reply_photo(chart, caption=msg, parse_mode="Markdown")

        else:

            await q.message.reply_text(msg, parse_mode="Markdown")



    # REMOVE ETF

    elif data.startswith("remove:"):

        ticker = data.split(":")[1].upper()

        remove_sub(chat_id, ticker)

        await q.message.reply_text(f"🗑 {ticker} removed ✔")

        await show_list_inline(chat_id, context)



    # REBOUND TOGGLE

    elif data.startswith("rebound:"):

        ticker = data.split(":")[1].upper()

        new = toggle_rebound(chat_id, ticker)

        await q.message.reply_text(f"🔁 {ticker} rebound {'ON' if new else 'OFF'} ✔")



    # CHOOSE THRESHOLD

    elif data.startswith("threshold:"):

        ticker = data.split(":")[1].upper()

        buttons = [[InlineKeyboardButton(f"{p}%", callback_data=f"setth:{ticker}:{p}")] for p in ["3","5","7","10","15","20"]]

        await q.message.reply_text("📉 Select drawdown threshold:", reply_markup=InlineKeyboardMarkup(buttons))



    # SET THRESHOLD

    elif data.startswith("setth:"):

        _, ticker, val = data.split(":")

        val = float(val)

        update_threshold(chat_id, ticker, val)

        await q.message.reply_text(f"✔ {ticker} threshold = {val}% ✔")



async def show_list_inline(chat_id, context):

    rows = get_subs(chat_id)

    if not rows:

        return await context.bot.send_message(chat_id, "❗ No ETFs")



    kb = []

    for t, th, rb in rows:

        kb.append([

            InlineKeyboardButton("📊", callback_data=f"status:{t.upper()}"),

            InlineKeyboardButton(f"📉 {th}%", callback_data=f"threshold:{t.upper()}"),

            InlineKeyboardButton("🔁 ON" if rb else "🔁 OFF", callback_data=f"rebound:{t.upper()}"),

            InlineKeyboardButton("🗑", callback_data=f"remove:{t.upper()}")

        ])




    await context.bot.send_message(chat_id, "📌 *My ETFs:* 👇", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))
