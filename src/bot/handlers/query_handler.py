import logging
import re
from datetime import date

from telegram import Update
from telegram.ext import ContextTypes

from config import ALLOWED_TELEGRAM_USER_ID
from database.queries import get_meals_for_date, get_recent_conversation, add_conversation_entry
from ai.claude_client import answer_question

logger = logging.getLogger(__name__)

HEBREW_CHARS = re.compile(r"[\u0590-\u05FF]")


def _build_meal_history(meals: list, recent_convos: list) -> str:
    lines = []

    if meals:
        lines.append("Meals logged today:")
        for m in meals:
            cal_str = f"{round(m.calories)} kcal" if m.calories else "?"
            prot_str = f"{round(m.protein_g)}g protein" if m.protein_g else "?"
            cat = m.meal_category or "unknown category"
            lines.append(f"  - {m.meal_name} ({cat}): {cal_str}, {prot_str}")
    else:
        lines.append("No meals logged today yet.")

    if recent_convos:
        lines.append("\nRecent conversation:")
        for conv in reversed(recent_convos):
            lines.append(f"  User: {conv.message_text}")
            if conv.response_text:
                lines.append(f"  Bot: {conv.response_text}")

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
