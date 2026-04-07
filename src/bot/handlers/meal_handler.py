import re
import logging
from datetime import date, datetime

from telegram import Update
from telegram.ext import ContextTypes

from config import ALLOWED_TELEGRAM_USER_ID, MEAL_CATEGORIES, REQUIRED_MEAL_CATEGORIES
from database.queries import add_meal_log, get_daily_totals, get_logged_categories_for_date
from external_apis import lookup_food
from bot.utils.formatters import format_meal_logged, format_daily_totals

logger = logging.getLogger(__name__)

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

    reply_lines = []
    all_logged_items: list[dict] = []

    for category, description in categories.items():
        food_names = _food_items_from_description(description)
        category_items: list[dict] = []

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
            }

            try:
                add_meal_log(meal_entry)
            except Exception as e:
                logger.error("add_meal_log failed: %s", e)

            category_items.append({
                **meal_entry,
                "estimated": food_data.get("estimated", False),
            })

        all_logged_items.extend(category_items)
        line = format_meal_logged(category, category_items, lang)
        if line:
            reply_lines.append(line)

    totals = get_daily_totals(today)
    totals_line = format_daily_totals(totals, lang)
    reply_lines.append(totals_line)

    logged_categories = get_logged_categories_for_date(today)
    if all(cat in logged_categories for cat in REQUIRED_MEAL_CATEGORIES):
        if lang == "he":
            reply_lines.append("\nכל הארוחות העיקריות נרשמו! אשלח סיכום ב-21:00 🌙")
        else:
            reply_lines.append("\nAll main meals logged! I'll send a summary at 21:00 🌙")

    await update.message.reply_text("\n".join(reply_lines))
