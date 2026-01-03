from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from db import add_etf, get_all_etfs
from utils import get_main_menu_keyboard


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /start command - show main menu."""
    welcome_text = (
        "👋 Вітаю! Я BuyLow Bot.\n\n"
        "Я допоможу відстежувати ціни на ETF та сповіщати, коли вони досягнуть цільового рівня.\n\n"
        "Оберіть дію:"
    )
    
    await update.message.reply_text(
        welcome_text,
        reply_markup=get_main_menu_keyboard()
    )


async def add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /add command."""
    if not context.args:
        await update.message.reply_text(
            "❗ Використання: /add AAPL\n\n"
            "Або використайте кнопку ➕ Add ETF",
            reply_markup=get_main_menu_keyboard()
        )
        return

    ticker = context.args[0].upper()
    add_etf(ticker)

    await update.message.reply_text(
        f"✅ {ticker} додано",
        reply_markup=get_main_menu_keyboard()
    )
