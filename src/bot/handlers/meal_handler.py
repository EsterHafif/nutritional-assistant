import re
import logging
from datetime import date, datetime

from telegram import Update
from telegram.ext import ContextTypes

from config import ALLOWED_TELEGRAM_USER_ID, MEAL_CATEGORIES, REQUIRED_MEAL_CATEGORIES
from database.queries import add_meal_log, get_daily_totals, get_logged_categories_for_date
from external_apis import lookup_food
from bot.utils.formatters import format_meal_logged, format_daily_totals
from bot.utils.keyboards import confirm_with_edit_keyboard, edit_items_keyboard

logger = logging.getLogger(__name__)

PENDING_MEAL_KEY = "pending_meal_log"

CATEGORY_PATTERN = re.compile(
    r"(בוקר|ביניים|צהריים|אחר הצהריים|ערב)\s*[:\-]\s*(.*?)(?=(?:בוקר|ביניים|צהריים|אחר הצהריים|ערב)\s*[:\-]|$)",
    re.DOTALL,
)

HEBREW_CHARS = re.compile(r"[\u0590-\u05FF]")


def is_structured_meal_log(text: str) -> bool:
    return any(f"{cat}:" in text or f"{cat} :" in text for cat in MEAL_CATEGORIES)


def parse_meal_categories(text: str) -> dict[str, str]:
    matches = CATEGORY_PATTERN.findall(text)
    return {cat.strip(): desc.strip() for cat, desc in matches if desc.strip()}


def _detect_lang(text: str) -> str:
    return "he" if HEBREW_CHARS.search(text) else "en"


def _food_items_from_description(description: str) -> list[str]:
    lines = [l.strip() for l in description.splitlines() if l.strip()]
    items = []
    for line in lines:
        parts = re.split(r"[,،+]", line)
        for part in parts:
            part = part.strip().lstrip("•-–*").strip()
            if part:
                items.append(part)
    return items if items else [description.strip()]


_MEAL_NUTRIENT_KEYS = [
    "calories", "protein_g", "carbs_g", "fat_g", "fiber_g",
    "sugar_g", "calcium_mg", "magnesium_mg", "iron_mg",
]


def _scale_pending_item(item: dict, new_grams: float) -> dict:
    import copy
    item = copy.deepcopy(item)
    food_data = item.get("_food_data") or {}
    values_per = food_data.get("values_per", "per_serving")
    serving_size_g = food_data.get("serving_size_g") or 0

    if values_per == "per_100g":
        scale = new_grams / 100.0
        base = food_data
    elif serving_size_g > 0:
        scale = new_grams / serving_size_g
        base = food_data
    else:
        return item

    for k in _MEAL_NUTRIENT_KEYS:
        v = base.get(k)
        if v is not None:
            item[k] = round(v * scale, 2)
    if item.get("_food_data"):
        item["_food_data"]["serving_size_g"] = new_grams
    return item


def _render_pending_summary(pending: list) -> str:
    from collections import defaultdict
    by_cat: dict = defaultdict(list)
    for entry in pending:
        by_cat[entry["meal_category"]].append(entry)
    lines = []
    for cat, items in by_cat.items():
        cat_lines = []
        for item in items:
            name = item.get("meal_name", "?")
            cal = item.get("calories")
            prot = item.get("protein_g")
            parts = []
            if cal is not None:
                parts.append(f"{round(cal)} קל'")
            if prot is not None:
                parts.append(f"{round(prot, 1)}g חלבון")
            detail = " | ".join(parts)
            cat_lines.append(f"  • {name}" + (f" — {detail}" if detail else ""))
        lines.append(f"{cat}:\n" + "\n".join(cat_lines))
    return "\n\n".join(lines)


async def handle_meal_log(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_user or update.effective_user.id != ALLOWED_TELEGRAM_USER_ID:
        return

    text = update.message.text or ""
    lang = _detect_lang(text)
    today = date.today()

    categories = parse_meal_categories(text)
    if not categories:
        if lang == "he":
            await update.message.reply_text("לא הצלחתי לזהות קטגוריות ארוחה בהודעה.")
        else:
            await update.message.reply_text("Could not detect meal categories in the message.")
        return

    all_pending: list[dict] = []

    for category, description in categories.items():
        food_names = _food_items_from_description(description)
        for food_name in food_names:
            if not food_name:
                continue
            try:
                food_data = await lookup_food(food_name)
            except Exception as e:
                logger.error("lookup_food failed for %s: %s", food_name, e)
                food_data = {}

            meal_entry: dict = {
                "meal_name": food_data.get("meal_name") or food_name,
                "meal_category": category,
                "meal_date": today,
                "meal_time": datetime.now().time(),
                "calories": food_data.get("calories"),
                "protein_g": food_data.get("protein_g"),
                "carbs_g": food_data.get("carbs_g"),
                "fat_g": food_data.get("fat_g"),
                "fiber_g": food_data.get("fiber_g"),
                "sugar_g": food_data.get("sugar_g"),
                "calcium_mg": food_data.get("calcium_mg"),
                "magnesium_mg": food_data.get("magnesium_mg"),
                "iron_mg": food_data.get("iron_mg"),
                "source": food_data.get("source", "structured_log"),
                "confidence_score": food_data.get("confidence_score", 0.7),
                "data": {"original_input": food_name},
                "_food_data": food_data,
            }
            all_pending.append(meal_entry)

    if not all_pending:
        await update.message.reply_text("לא הצלחתי לזהות פריטים.")
        return

    context.user_data[PENDING_MEAL_KEY] = all_pending
    summary = _render_pending_summary(all_pending)
    await update.message.reply_text(
        f"{summary}\n\nלרשום הכל?",
        reply_markup=confirm_with_edit_keyboard("meal_confirm_yes", "meal_edit_start", "meal_confirm_no"),
    )


async def handle_meal_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    query = update.callback_query
    if not query:
        return False
    data = query.data or ""

    if data not in ("meal_confirm_yes", "meal_confirm_no", "meal_edit_start") and \
            not data.startswith("meal_ei_"):
        return False

    await query.answer()

    if data == "meal_confirm_no":
        context.user_data.pop(PENDING_MEAL_KEY, None)
        await query.edit_message_text("בסדר, לא נרשם.")
        return True

    if data == "meal_confirm_yes":
        pending = context.user_data.pop(PENDING_MEAL_KEY, [])
        if not pending:
            await query.edit_message_text("לא נמצאו נתונים.")
            return True
        today = date.today()
        from collections import defaultdict
        by_cat: dict = defaultdict(list)
        for entry in pending:
            by_cat[entry["meal_category"]].append(entry)
            entry.pop("_food_data", None)
            try:
                add_meal_log(entry)
            except Exception as e:
                logger.error("add_meal_log failed: %s", e)
        reply_lines = []
        for cat, items in by_cat.items():
            line = format_meal_logged(cat, items, "he")
            if line:
                reply_lines.append(line)
        totals = get_daily_totals(today)
        reply_lines.append(format_daily_totals(totals, "he"))
        logged_categories = get_logged_categories_for_date(today)
        if all(cat in logged_categories for cat in REQUIRED_MEAL_CATEGORIES):
            reply_lines.append("\nכל הארוחות העיקריות נרשמו! אשלח סיכום ב-21:00 🌙")
        await query.edit_message_text("\n\n".join(reply_lines))
        return True

    if data == "meal_edit_start":
        pending = context.user_data.get(PENDING_MEAL_KEY, [])
        if not pending:
            await query.edit_message_text("לא נמצאו נתונים.")
            return True
        await query.edit_message_text(
            "איזה פריט לשנות?",
            reply_markup=edit_items_keyboard(pending, "meal_ei_", "meal_confirm_yes"),
        )
        return True

    if data.startswith("meal_ei_"):
        try:
            idx = int(data[len("meal_ei_"):])
        except ValueError:
            return True
        pending = context.user_data.get(PENDING_MEAL_KEY, [])
        if idx >= len(pending):
            await query.edit_message_text("פריט לא נמצא.")
            return True
        item = pending[idx]
        name = item.get("meal_name", "פריט")
        food_data = item.get("_food_data") or {}
        serving_g = food_data.get("serving_size_g")
        cal = item.get("calories")
        hint_parts = []
        if serving_g:
            hint_parts.append(f"מנה נוכחית: {serving_g}g")
        if cal is not None:
            hint_parts.append(f"קלוריות: {round(cal)} קל'")
        hint = " | ".join(hint_parts)
        context.user_data["awaiting_edit_grams"] = True
        context.user_data["edit_context"] = {"flow": "meal", "item_idx": idx}
        await query.edit_message_text(
            f"{name}\n{hint}\n\nכמה גרם?".strip()
        )
        return True

    return False
