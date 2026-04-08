from telegram import InlineKeyboardButton, InlineKeyboardMarkup


def confirm_keyboard(yes_data: str, no_data: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("כן ✓", callback_data=yes_data),
        InlineKeyboardButton("לא ✗", callback_data=no_data),
    ]])


def save_to_db_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("כן, שמרי במאגר 📦", callback_data="db_save_yes"),
        InlineKeyboardButton("לא תודה", callback_data="db_save_no"),
    ]])


def steady_meal_save_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("כן, שמרי 📌", callback_data="steady_save_yes"),
        InlineKeyboardButton("לא תודה", callback_data="steady_save_no"),
    ]])


def category_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🌅 בוקר", callback_data="cat_בוקר"),
         InlineKeyboardButton("🥪 ביניים", callback_data="cat_ביניים")],
        [InlineKeyboardButton("☀️ צהריים", callback_data="cat_צהריים"),
         InlineKeyboardButton("🍎 אחר הצהריים", callback_data="cat_אחר הצהריים")],
        [InlineKeyboardButton("🌙 ערב", callback_data="cat_ערב")],
    ])
