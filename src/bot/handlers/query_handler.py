import logging
import re

from telegram import Update
from telegram.ext import ContextTypes

from config import ALLOWED_TELEGRAM_USER_ID, MEAL_CATEGORIES
from datetime import date, datetime
from database.queries import get_meals_for_date, get_recent_conversation, add_conversation_entry, add_meal_log, get_daily_totals, get_weekly_data
from ai.claude_client import answer_question, extract_meals_from_conversation, generate_daily_summary, generate_weekly_summary

logger = logging.getLogger(__name__)

HEBREW_CHARS = re.compile(r"[\u0590-\u05FF]")
_WEEKLY_SUMMARY_KEYWORDS = re.compile(r"שבועי|weekly", re.IGNORECASE)
_SUMMARY_KEYWORDS = re.compile(r"סיכום|summarize|summary|סכמי|תסכמי", re.IGNORECASE)


def _current_week_range():
    from datetime import timedelta
    today = date.today()
    weekday = today.isoweekday() % 7  # Sun=0 … Sat=6
    start = today - timedelta(days=weekday)
    return start, today


def _build_meal_history(meals: list, recent_convos: list) -> str:
    lines = []

    if meals:
        lines.append("Meals logged today:")
        for m in meals:
            cal_str = f"{round(m['calories'])} kcal" if m.get("calories") else "?"
            prot_str = f"{round(m['protein_g'])}g protein" if m.get("protein_g") else "?"
            cat = m.get("meal_category") or "unknown category"
            lines.append(f"  - {m['meal_name']} ({cat}): {cal_str}, {prot_str}")
    else:
        lines.append("No meals logged today yet.")

    if recent_convos:
        lines.append("\nRecent conversation:")
        for conv in reversed(recent_convos):
            lines.append(f"  User: {conv['message_text']}")
            if conv.get("response_text"):
                lines.append(f"  Bot: {conv['response_text']}")

    return "\n".join(lines)


async def handle_query(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_user or update.effective_user.id != ALLOWED_TELEGRAM_USER_ID:
        return

    question = update.message.text or ""
    if not question.strip():
        return

    today = date.today()

    try:
        meals = get_meals_for_date(today)
    except Exception as e:
        logger.error("get_meals_for_date failed: %s", e)
        meals = []

    # Weekly summary check must come before daily summary check
    if _WEEKLY_SUMMARY_KEYWORDS.search(question):
        week_start, week_end = _current_week_range()
        is_partial = week_end.isoweekday() % 7 != 6  # not Saturday = partial
        try:
            weekly_data = get_weekly_data(week_start, week_end)
        except Exception as e:
            logger.error("handle_query weekly: get_weekly_data failed: %s", e)
            weekly_data = {"days": [], "totals": {}, "averages": {}, "days_fully_logged": 0, "days_with_any_data": 0, "exercise": {"sessions": 0, "total_minutes": 0, "total_kcal": 0}}
        try:
            answer = await generate_weekly_summary(weekly_data, week_start, week_end, is_partial=is_partial)
        except Exception as e:
            logger.error("handle_query weekly: generate_weekly_summary failed: %s", e)
            answer = "מצטערת, לא הצלחתי ליצור סיכום שבועי כרגע."
        await update.message.reply_text(answer)
        try:
            add_conversation_entry(question, answer)
        except Exception as e:
            logger.error("add_conversation_entry failed: %s", e)
        return

    # Route summary requests to the dedicated summary generator
    if _SUMMARY_KEYWORDS.search(question):
        try:
            totals = get_daily_totals(today)
        except Exception as e:
            logger.error("get_daily_totals failed: %s", e)
            totals = {}
        meals_by_category: dict = {}
        for cat in MEAL_CATEGORIES:
            items = [
                {"meal_name": m["meal_name"], "calories": m["calories"], "protein_g": m["protein_g"]}
                for m in meals if m.get("meal_category") == cat
            ]
            if items:
                meals_by_category[cat] = items
        try:
            answer = await generate_daily_summary(totals, meals_by_category)
        except Exception as e:
            logger.error("generate_daily_summary failed: %s", e)
            answer = "מצטערת, לא הצלחתי ליצור סיכום כרגע."
        await update.message.reply_text(answer)
        try:
            add_conversation_entry(question, answer)
        except Exception as e:
            logger.error("add_conversation_entry failed: %s", e)
        return

    try:
        recent_convos = get_recent_conversation(limit=5)
    except Exception as e:
        logger.error("get_recent_conversation failed: %s", e)
        recent_convos = []

    meal_history = _build_meal_history(meals, recent_convos)

    try:
        answer = await answer_question(question, meal_history)
    except Exception as e:
        logger.error("answer_question failed: %s", e)
        is_hebrew = bool(HEBREW_CHARS.search(question))
        if is_hebrew:
            answer = "מצטערת, לא הצלחתי לעבד את השאלה כרגע."
        else:
            answer = "Sorry, I couldn't process your question right now."

    await update.message.reply_text(answer)

    try:
        add_conversation_entry(question, answer)
    except Exception as e:
        logger.error("add_conversation_entry failed: %s", e)

    # Extract and persist any food items that were estimated in this exchange
    try:
        conversation_text = f"User: {question}\nAssistant: {answer}"
        extracted = await extract_meals_from_conversation(conversation_text)
        if extracted:
            already_logged_names = {m["meal_name"].strip().lower() for m in meals if m.get("meal_name")}
            for item in extracted:
                meal_name = item.get("meal_name", "").strip()
                if not meal_name:
                    continue
                # Skip if this food name is already in today's log (avoid duplicates)
                if meal_name.lower() in already_logged_names:
                    continue
                meal_data = {
                    "meal_name": meal_name,
                    "meal_category": item.get("meal_category"),
                    "meal_date": date.today(),
                    "meal_time": datetime.now().time(),
                    "calories": item.get("calories"),
                    "protein_g": item.get("protein_g"),
                    "carbs_g": item.get("carbs_g"),
                    "fat_g": item.get("fat_g"),
                    "fiber_g": item.get("fiber_g"),
                    "calcium_mg": item.get("calcium_mg"),
                    "iron_mg": item.get("iron_mg"),
                    "source": "estimated",
                    "confidence_score": item.get("confidence_score", 0.75),
                }
                add_meal_log(meal_data)
                already_logged_names.add(meal_name.lower())
    except Exception as e:
        logger.error("extract_meals_from_conversation failed: %s", e)
