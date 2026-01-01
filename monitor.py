import yfinance as yf
from db import get_conn
from utils import escape_md


async def check_prices(context):
    bot = context.bot

    with get_conn() as conn:
        rows = conn.execute(
            "SELECT chat_id, ticker, threshold FROM subs"
        ).fetchall()

    for chat_id, ticker, threshold in rows:
        try:
            price = yf.Ticker(ticker).fast_info["lastPrice"]
        except Exception:
            continue

        if price <= threshold:
            await bot.send_message(
                chat_id,
                f"📉 *{escape_md(ticker)}* = *{price:.2f}* ≤ {threshold}",
                parse_mode="MarkdownV2"
            )

            with get_conn() as conn:
                conn.execute(
                    "DELETE FROM subs WHERE chat_id=? AND ticker=?",
                    (chat_id, ticker)
                )
