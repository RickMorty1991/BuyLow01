from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters
)
from callbacks import callback_handler, main_menu
from handlers import text_handler
from db import init_db, add_etf

BOT_TOKEN = "TOKEN_HERE"


async def start(update, context):
    context.bot_data["chat_id"] = update.effective_chat.id

    # тестові ETF
    add_etf("SPY")
    add_etf("QQQ")

    await update.message.reply_text(
        "📊 ETF Monitor",
        reply_markup=main_menu()
    )


def main():
    init_db()

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(callback_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))

    app.run_polling()


if __name__ == "__main__":
    main()
