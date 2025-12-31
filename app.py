from telegram.ext import Application

from config import BOT_TOKEN

from callbacks import callbacks

from monitor import monitor_loop

import threading



def main():

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start_cmd))

    app.add_handler(CallbackQueryHandler(callbacks))



    threading.Thread(target=monitor_loop, args=(app,), daemon=True).start()

    print("Bot running...")

    app.run_polling()



if __name__ == "__main__":

    main()
