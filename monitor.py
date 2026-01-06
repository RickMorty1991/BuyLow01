import yfinance as yf
from db import get_monitor_data, update_last_price


async def check_prices(context):
    data = get_monitor_data()

    for ticker, target, rebound, last_price in data:
        if not target:
            continue

        info = yf.Ticker(ticker).fast_info
        price = info.get("lastPrice")

        if not price:
            continue

        trigger = False

        if rebound:
            if last_price and last_price < target and price > last_price:
                trigger = True
        else:
            if price <= target:
                trigger = True

        if trigger:
            await context.bot.send_message(
                chat_id=context.bot_data["chat_id"],
                text=(
                    f"🚨 {ticker}\n"
                    f"Ціна: {price}\n"
                    f"Поріг: {target}"
                )
            )

        update_last_price(ticker, price)
