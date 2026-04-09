import logging
from telegram import Update
from telegram.ext import ContextTypes
from config import ALLOWED_TELEGRAM_USER_ID

logger = logging.getLogger(__name__)


async def handle_edit_grams_reply(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_user or update.effective_user.id != ALLOWED_TELEGRAM_USER_ID:
        return

    text = (update.message.text or "").strip().replace(",", ".")
    try:
        new_grams = float(text)
        if new_grams <= 0:
            raise ValueError
    except (ValueError, TypeError):
        await update.message.reply_text("אנא כתבי מספר חיובי (לדוגמה: 150)")
        return

    context.user_data.pop("awaiting_edit_grams", None)
    flow_ctx = context.user_data.pop("edit_context", {})
    flow = flow_ctx.get("flow")

    if flow == "label":
        await _apply_label_edit(update, context, new_grams)
    elif flow == "dish":
        await _apply_dish_edit(update, context, new_grams)
    elif flow == "meal":
        await _apply_meal_edit(update, context, new_grams, flow_ctx.get("item_idx", 0))
    elif flow == "steady":
        await _apply_steady_edit(update, context, new_grams, flow_ctx.get("item_idx", 0))
    else:
        await update.message.reply_text("לא נמצא תהליך עריכה פעיל.")


async def _apply_label_edit(update: Update, context: ContextTypes.DEFAULT_TYPE, new_grams: float) -> None:
    from bot.handlers.photo_handler import (
        PENDING_LABEL_KEY, _scale_label_to_grams, _render_summary,
    )
    from bot.utils.keyboards import confirm_with_edit_keyboard

    label_data = context.user_data.get(PENDING_LABEL_KEY)
    if not label_data:
        await update.message.reply_text("לא נמצאו נתוני תווית. שלחי את התמונה שוב.")
        return

    label_data = _scale_label_to_grams(label_data, new_grams)
    context.user_data[PENDING_LABEL_KEY] = label_data

    product_name = label_data.get("product_name", "המוצר")
    summary = _render_summary(label_data)
    await update.message.reply_text(
        f"{summary}\n\nלרשום את {product_name} כארוחה להיום?",
        reply_markup=confirm_with_edit_keyboard("label_confirm_yes", "label_edit", "label_confirm_no"),
    )


async def _apply_dish_edit(update: Update, context: ContextTypes.DEFAULT_TYPE, new_grams: float) -> None:
    from bot.handlers.photo_handler import (
        PENDING_DISH_KEY, _scale_dish_to_grams, _render_dish_summary,
    )
    from bot.utils.keyboards import confirm_with_edit_keyboard

    dish = context.user_data.get(PENDING_DISH_KEY)
    if not dish:
        await update.message.reply_text("לא נמצאו נתוני מנה. שלחי את התמונה שוב.")
        return

    dish = _scale_dish_to_grams(dish, new_grams)
    context.user_data[PENDING_DISH_KEY] = dish

    summary = _render_dish_summary(dish)
    await update.message.reply_text(
        f"{summary}\n\nלרשום את זה כארוחה להיום?",
        reply_markup=confirm_with_edit_keyboard("dish_confirm_yes", "dish_edit", "dish_confirm_no"),
    )


async def _apply_meal_edit(
    update: Update, context: ContextTypes.DEFAULT_TYPE, new_grams: float, item_idx: int
) -> None:
    from bot.handlers.meal_handler import (
        PENDING_MEAL_KEY, _scale_pending_item, _render_pending_summary,
    )
    from bot.utils.keyboards import confirm_with_edit_keyboard

    pending = context.user_data.get(PENDING_MEAL_KEY)
    if not pending or item_idx >= len(pending):
        await update.message.reply_text("לא נמצאו נתוני ארוחה.")
        return

    pending[item_idx] = _scale_pending_item(pending[item_idx], new_grams)
    context.user_data[PENDING_MEAL_KEY] = pending

    summary = _render_pending_summary(pending)
    await update.message.reply_text(
        f"{summary}\n\nלרשום הכל?",
        reply_markup=confirm_with_edit_keyboard("meal_confirm_yes", "meal_edit_start", "meal_confirm_no"),
    )


async def _apply_steady_edit(
    update: Update, context: ContextTypes.DEFAULT_TYPE, new_grams: float, item_idx: int
) -> None:
    from bot.handlers.steady_meal_handler import (
        PENDING_STEADY_MEAL_KEY, _scale_steady_item, _aggregate_nutrition, _format_breakdown,
    )
    from bot.utils.keyboards import confirm_with_edit_keyboard

    pending = context.user_data.get(PENDING_STEADY_MEAL_KEY)
    if not pending:
        await update.message.reply_text("לא נמצאו נתוני ארוחה קבועה.")
        return

    items = pending.get("items") or []
    if item_idx >= len(items):
        await update.message.reply_text("לא נמצא הפריט.")
        return

    items[item_idx] = _scale_steady_item(items[item_idx], new_grams)
    totals = _aggregate_nutrition(items)
    pending["items"] = items
    pending["totals"] = totals
    context.user_data[PENDING_STEADY_MEAL_KEY] = pending

    breakdown = _format_breakdown(items, totals)
    await update.message.reply_text(
        f"{breakdown}\n\nהנתונים נראים בסדר?",
        reply_markup=confirm_with_edit_keyboard(
            "steady_breakdown_ok", "steady_breakdown_edit", "steady_breakdown_cancel"
        ),
    )
