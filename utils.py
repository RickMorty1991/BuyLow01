import re
from telegram import InlineKeyboardButton, InlineKeyboardMarkup


def escape_md(text: str) -> str:
    return re.sub(r'([_\-*\[\]()~`>#+=|{}.!])', r'\\\1', text)


def get_main_menu_keyboard():
    """Create the main menu inline keyboard."""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("➕ Add ETF", callback_data="action:add"),
            InlineKeyboardButton("📌 My ETFs", callback_data="action:list")
        ],
        [
            InlineKeyboardButton("↘️ Set Threshold", callback_data="action:threshold"),
            InlineKeyboardButton("📈 Toggle Rebound", callback_data="action:rebound")
        ],
        [
            InlineKeyboardButton("🔄 Force Check All", callback_data="action:check"),
            InlineKeyboardButton("📊 Status", callback_data="action:status")
        ],
        [
            InlineKeyboardButton("❓ Help", callback_data="action:help")
        ]
    ])
