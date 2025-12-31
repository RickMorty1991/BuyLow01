import time

from config import CHECK_INTERVAL

from db import get_subs, c, db_lock, db, toggle_rebound

from price_helpers import get_price_now, get_ath_52w, calc_change, build_chart_bytes



def monitor_loop(app):

    while True:

        rows = get_subs()

        for chat_id, ticker, threshold, rb, last_alerted, rebound_sent, price_ago in rows:

            try:

                now = get_price_now(ticker)

                ath, ath_date = get_ath_52w(ticker)

                if now is None or ath is None:

                    continue



                dd = (ath - now) / ath * 100

                change = calc_change(now, price_ago)



                msg = (

                    f"⚠️ *{ticker}*\n"

                    f"💰 {now:.2f} USD\n"

                    f"📆 ATH {ath:.2f} ({ath_date})\n"

                    f"📉 Drawdown {dd:.2f}%"

                )

                if change is not None:

                    msg += f"\nΔ365 {change:.2f}%"



                # ALERT

                if dd >= threshold and last_alerted == 0:

                    app.bot.send_message(chat_id, msg, parse_mode="Markdown")

                    with db_lock:

                        c.execute("UPDATE subs SET last_alerted=1 WHERE chat_id=? AND ticker=?", (chat_id, ticker))

                        db.commit()



                # REBOUND

                if dd < threshold and rb == 1 and last_alerted == 1 and rebound_sent == 0:

                    app.bot.send_message(chat_id, f"📈 *{ticker} rebound!*", parse_mode="Markdown")

                    with db_lock:

                        c.execute("UPDATE subs SET rebound_sent=1 WHERE chat_id=? AND ticker=?", (chat_id, ticker))

                        db.commit()



            except Exception as e:

                print("Monitor error:", e)




        time.sleep(CHECK_INTERVAL)
