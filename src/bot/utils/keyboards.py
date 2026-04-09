from telegram import InlineKeyboardButton, InlineKeyboardMarkup


def confirm_with_edit_keyboard(yes_data: str, edit_data: str, no_data: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("כן ✓", callback_data=yes_data),
        InlineKeyboardButton("✏️ עריכה", callback_data=edit_data),
        InlineKeyboardButton("לא ✗", callback_data=no_data),
    ]])


def edit_items_keyboard(items: list, prefix: str, cancel_data: str) -> InlineKeyboardMarkup:
    buttons = []
    for i, item in enumerate(items):
        name = item.get("meal_name") or item.get("product_name") or f"פריט {i + 1}"
        cal = item.get("calories")
        label = f"{name} ({round(cal)} קל')" if cal is not None else name
        buttons.append([InlineKeyboardButton(label, callback_data=f"{prefix}{i}")])
    buttons.append([InlineKeyboardButton("ביטול", callback_data=cancel_data)])
    return InlineKeyboardMarkup(buttons)


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


def steady_meal_fuzzy_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("כן, זה נכון ✓", callback_data="steady_fuzzy_yes"),
        InlineKeyboardButton("לא, אמדי ✗", callback_data="steady_fuzzy_no"),
    ]])


def category_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🌅 בוקר", callback_data="cat_בוקר"),
         InlineKeyboardButton("🥪 ביניים", callback_data="cat_ביניים")],
        [InlineKeyboardButton("☀️ צהריים", callback_data="cat_צהריים"),
         InlineKeyboardButton("🍎 אחר הצהריים", callback_data="cat_אחר הצהריים")],
        [InlineKeyboardButton("🌙 ערב", callback_data="cat_ערב")],
    ])
