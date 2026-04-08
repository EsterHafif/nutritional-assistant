import logging
from telegram import Update
from telegram.ext import ContextTypes
from config import ALLOWED_TELEGRAM_USER_ID
from ai.claude_client import parse_meal_text
from database.queries import add_food_db_item, search_food_db
from bot.utils.keyboards import steady_meal_save_keyboard

logger = logging.getLogger(__name__)

PENDING_STEADY_MEAL_KEY = "pending_steady_meal"
TRIGGER = "ארוחה קבועה"

NUTRIENT_FIELDS = [
    "calories", "protein_g", "carbs_g", "fat_g", "fiber_g",
    "sugar_g", "calcium_mg", "magnesium_mg", "iron_mg",
]


def is_steady_meal_creation(text: str) -> bool:
    return text.strip().startswith(TRIGGER)


def _extract_description(text: str) -> str:
    text = text.strip()
    for sep in [":", "-"]:
        if TRIGGER + sep in text:
            return text[text.index(TRIGGER + sep) + len(TRIGGER) + 1:].strip()
    return text[len(TRIGGER):].strip()


def _aggregate_nutrition(items: list[dict]) -> dict:
    totals: dict = {f: None for f in NUTRIENT_FIELDS}
    for item in items:
        for f in NUTRIENT_FIELDS:
            val = item.get(f)
            if val is not None:
                totals[f] = (totals[f] or 0) + val
    return totals


def _format_nutrition_summary(data: dict) -> str:
    lines = []
    if data.get("calories") is not None:
        lines.append(f"קלוריות: {round(data['calories'])} קל'")
    if data.get("protein_g") is not None:
        lines.append(f"חלבון: {round(data['protein_g'], 1)}g")
    if data.get("carbs_g") is not None:
        lines.append(f"פחמימות: {round(data['carbs_g'], 1)}g")
    if data.get("fat_g") is not None:
        lines.append(f"שומן: {round(data['fat_g'], 1)}g")
    if data.get("fiber_g") is not None:
        lines.append(f"סיבים: {round(data['fiber_g'], 1)}g")
    if data.get("calcium_mg") is not None:
        lines.append(f"סידן: {round(data['calcium_mg'])}mg")
    return "\n".join(lines)


async def handle_steady_meal_creation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_user or update.effective_user.id != ALLOWED_TELEGRAM_USER_ID:
        return

    context.user_data.pop(PENDING_STEADY_MEAL_KEY, None)
    context.user_data.pop("awaiting_steady_meal_name", None)

    text = update.message.text or ""
    description = _extract_description(text)

    if not description:
        await update.message.reply_text(
            "כתבי את תיאור הארוחה הקבועה אחרי הנקודתיים, לדוגמה:\n"
            "ארוחה קבועה: קפה עם 90 מל חלב"
        )
        return

    await update.message.reply_text("מחשבת ערכים תזונתיים... ⏳")

    items = await parse_meal_text(description)
    if not items:
        await update.message.reply_text(
            "לא הצלחתי לחשב ערכים תזונתיים לתיאור הזה. נסי לנסח מחדש."
        )
        return

    nutrition = _aggregate_nutrition(items)

    context.user_data[PENDING_STEADY_MEAL_KEY] = {
        "description": description,
        "component_items": items,
        **nutrition,
    }
    context.user_data["awaiting_steady_meal_name"] = True

    summary = _format_nutrition_summary(nutrition)
    await update.message.reply_text(
        f"חישבתי את הערכים התזונתיים:\n{summary}\n\nאיך לקרוא לארוחה הקבועה הזו?"
    )


async def handle_steady_meal_name_reply(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    if not context.user_data.get("awaiting_steady_meal_name"):
        return False
    if not update.effective_user or update.effective_user.id != ALLOWED_TELEGRAM_USER_ID:
        return False
    if not context.user_data.get(PENDING_STEADY_MEAL_KEY):
        context.user_data.pop("awaiting_steady_meal_name", None)
        return False

    name = update.message.text.strip()[:255]
    context.user_data.pop("awaiting_steady_meal_name", None)

    pending = context.user_data[PENDING_STEADY_MEAL_KEY]
    pending["name"] = name

    existing = search_food_db(name)
    overwrite_note = ""
    if existing and getattr(existing, "source", None) == "steady_meal":
        pending["overwrite_id"] = getattr(existing, "id", None)
        overwrite_note = "\n⚠️ ארוחה קבועה בשם זה כבר קיימת — שמירה תדרוס אותה."

    summary = _format_nutrition_summary(pending)
    await update.message.reply_text(
        f"לשמור את '{name}' כארוחה קבועה?{overwrite_note}\n\n{summary}",
        reply_markup=steady_meal_save_keyboard(),
    )
    return True


async def handle_steady_meal_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    query = update.callback_query
    if not query or query.data not in ("steady_save_yes", "steady_save_no"):
        return False

    await query.answer()

    if query.data == "steady_save_no":
        context.user_data.pop(PENDING_STEADY_MEAL_KEY, None)
        await query.edit_message_text("בסדר, לא נשמר.")
        return True

    pending = context.user_data.pop(PENDING_STEADY_MEAL_KEY, {})
    if not pending:
        await query.edit_message_text("לא נמצאו נתונים לשמירה.")
        return True

    name = pending.get("name", "ארוחה קבועה")
    item_data = {
        "product_name": name,
        "source": "steady_meal",
        "values_per": "per_serving",
        "calories": pending.get("calories"),
        "protein_g": pending.get("protein_g"),
        "carbs_g": pending.get("carbs_g"),
        "fat_g": pending.get("fat_g"),
        "fiber_g": pending.get("fiber_g"),
        "sugar_g": pending.get("sugar_g"),
        "calcium_mg": pending.get("calcium_mg"),
        "magnesium_mg": pending.get("magnesium_mg"),
        "iron_mg": pending.get("iron_mg"),
        "data": {
            "description": pending.get("description"),
            "components": pending.get("component_items"),
        },
    }

    try:
        add_food_db_item(item_data)
        summary = _format_nutrition_summary(pending)
        await query.edit_message_text(
            f"✓ '{name}' נשמרה כארוחה קבועה 📌\n{summary}\n\nבפעם הבאה פשוט כתבי את השם!"
        )
    except Exception as e:
        logger.error("add_food_db_item steady_meal failed: %s", e)
        await query.edit_message_text("שגיאה בשמירה, נסי שוב.")

    return True
