# ================= CONFIG =================
BOT_TOKEN = os.getenv("TELEGRAM_TOKEN")  # або встав напряму сюди токен, якщо не використовуєш ENV
CHECK_INTERVAL = 900  # 15 хв
DB_PATH = "etf.db"

lock = threading.Lock()

        if df.empty or ath is None:
            return None
        buf = io.BytesIO()
        plt.figure(figsize=(6, 3))
        plt.plot(df.index, df["Close"].values)
        plt.axhline(ath, linestyle="--")
        plt.title(ticker)
        plt.tight_layout()
        plt.savefig(buf, format="png")
        plt.close()
        buf.seek(0)
        return buf
    except:
        return None

# ================= MENU =================
async def show_list(update: Update):
    chat_id = update.effective_chat.id
    with lock:
        rows = c.execute(
            "SELECT ticker, threshold, rebound_enabled FROM subs WHERE chat_id=?",
            (chat_id,),
        ).fetchall()

    if not rows:
        return await update.message.reply_text("❗ No ETFs subscribed")

    kb = []
    for t, th, rb in rows:
        kb.append([
            InlineKeyboardButton("📊 Status", callback_data=f"status:{t}"),
            InlineKeyboardButton(f"📉 {th}%", callback_data=f"threshold:{t}"),
            InlineKeyboardButton(f"🔁 {'ON' if rb else 'OFF'}", callback_data=f"rebound:{t}"),
            InlineKeyboardButton("🗑 Remove", callback_data=f"remove:{t}")
        ])

    await update.message.reply_text(
        "📌 *My ETFs:*",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(kb),
    )

# ================= COMMANDS =================
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    with lock:
        if not c.execute("SELECT 1 FROM subs WHERE chat_id=?", (chat_id,)).fetchone():
            for t, th in [("SPY", 4), ("QQQ", 7)]:
                c.execute(
                    "INSERT OR IGNORE INTO subs(chat_id,ticker,threshold) VALUES(?,?,?)",
                    (chat_id, t, th),
                )
            db.commit()

    await show_list(update)

async def add_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if not context.args:
        return await update.message.reply_text("❗ Format: /add <ticker>")
    ticker = context.args[0].upper()

    now = get_price_now(ticker)
    ath, ath_date = get_ath_52w(ticker)
    if now is None or ath is None:
        return await update.message.reply_text("❗ Invalid ticker or no data")

    chart = build_chart(ticker, ath)
    text = f"✔ *{ticker} added*\n💰 {now:.2f} USD\n📆 ATH {ath:.2f} ({ath_date})"

    if chart:
        await update.message.reply_photo(chart, caption=text, parse_mode="Markdown")
    else:
        await update.message.reply_text(text, parse_mode="Markdown")

    with lock:
        c.execute(
            "INSERT OR IGNORE INTO subs(chat_id,ticker,threshold) VALUES(?,?,?)",
            (chat_id, ticker, 5),
        )
        db.commit()

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("ℹ️ Use /start, /add <ticker>, and buttons to manage ETFs.")

# ================= CALLBACKS =================
async def callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    chat_id = q.message.chat.id
    data = q.data

    if data.startswith("status:"):
        ticker = data.split(":")[1]
        now = get_price_now(ticker)
        ath, ath_date = get_ath_52w(ticker)
        if now is None or ath is None:
            return await q.message.reply_text("❗ No data")

        dd = (ath - now) / ath * 100
        ch = calc_change(now, ath)

        text = (
            f"📊 *{ticker}*\n"
            f"💰 {now:.2f} USD\n"
            f"📆 ATH {ath:.2f} ({ath_date})\n"
            f"📉 Drawdown {dd:.2f}%"
        )
        chart = build_chart(ticker, ath)
        if chart:
            await q.message.reply_photo(chart, caption=text, parse_mode="Markdown")
        else:
            await q.message.reply_text(text, parse_mode="Markdown")

    elif data.startswith("remove:"):
        ticker = data.split(":")[1]
        with lock:
            c.execute("DELETE FROM subs WHERE chat_id=? AND ticker=?", (chat_id, ticker))
            db.commit()
        await q.message.reply_text(f"🗑 {ticker} removed ✔")
        await show_list(update)

    elif data.startswith("rebound:"):
        ticker = data.split(":")[1]
        with lock:
            row = c.execute("SELECT rebound_enabled FROM subs WHERE chat_id=? AND ticker=?", (chat_id, ticker)).fetchone()
            new = 0 if row and row[0] == 1 else 1
            c.execute("UPDATE subs SET rebound_enabled=?, rebound_sent=0 WHERE chat_id=? AND ticker=?", (new, chat_id, ticker))
            db.commit()
        await q.message.reply_text(f"🔁 {ticker} rebound {'ON' if new else 'OFF'} ✔")

    elif data.startswith("threshold:"):
        ticker = data.split(":")[1]
        kb = [[InlineKeyboardButton(f"{p}%", callback_data=f"threshold_set:{ticker}:{p}")] for p in ["3","5","7","10","15"]]
        await q.message.reply_text("📉 Select drawdown threshold:", reply_markup=InlineKeyboardMarkup(kb))

    elif data.startswith("threshold_set:"):
        _, ticker, val = data.split(":")
        val = float(val)
        with lock:
            c.execute("UPDATE subs SET threshold=?, last_alerted=0, rebound_sent=0 WHERE chat_id=? AND ticker=?", (val, chat_id, ticker))
            db.commit()
        await q.message.reply_text(f"✔ {ticker} threshold = {val}% ✔")

# ================= MONITOR =================
def monitor_loop_runner(app):
    while True:
        with lock:
            rows = c.execute("SELECT chat_id, ticker, threshold, last_alerted FROM subs").fetchall()

        for chat_id, ticker, th, alerted in rows:
            now = get_price_now(ticker)
            ath, _ = get_ath_52w(ticker)
            if now is None or ath is None:
                continue
            dd = (ath - now) / ath * 100
            if dd >= th and alerted == 0:
                app.bot.send_message(chat_id, f"⚠️ {ticker} drawdown {dd:.2f}%")
                with lock:
                    c.execute("UPDATE subs SET last_alerted=1 WHERE chat_id=? AND ticker=?", (chat_id, ticker))
                    db.commit()

        time.sleep(CHECK_INTERVAL)

# ================= RUN =================
def main():
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("add", add_cmd))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CallbackQueryHandler(callbacks))

    threading.Thread(target=monitor_loop_runner, args=(app,), daemon=True).start()

    print("Bot running...")
    app.run_polling()

if __name__ == "__main__":
    main()
