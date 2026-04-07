from telegram import InlineKeyboardButton, InlineKeyboardMarkup


def confirm_keyboard(yes_data: str, no_data: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("כן ✓", callback_data=yes_data),
        InlineKeyboardButton("לא ✗", callback_data=no_data),
    ]])
