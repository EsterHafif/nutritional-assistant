import logging
import re
import asyncio

from telegram import Update
from telegram.ext import ContextTypes

from config import ALLOWED_TELEGRAM_USER_ID, MEAL_CATEGORIES
from datetime import date, datetime
from database.queries import (
    get_meals_for_date, get_recent_conversation, add_conversation_entry,
    add_meal_log, get_daily_totals, get_weekly_data,
    get_exercise_for_date, get_steady_meals,
    update_meal_log, delete_meal_log,
    delete_exercise, update_exercise,
    delete_food_db_item, update_food_db_item, find_food_db_items, insert_exercise,
    get_fitbit_stats_for_date,
)
from ai.claude_client import answer_with_tools, generate_daily_summary, generate_weekly_summary
from external_apis import lookup_food as _lookup_food

logger = logging.getLogger(__name__)

HEBREW_CHARS = re.compile(r"[\u0590-\u05FF]")


async def _silent_fitbit_sync() -> None:
    from external_apis.fitbit import sync_fitbit_activities
    from datetime import date
    try:
        await asyncio.to_thread(sync_fitbit_activities, date.today())
    except Exception as e:
        logger.error("silent fitbit sync: %s", e)
_WEEKLY_SUMMARY_KEYWORDS = re.compile(r"שבועי|weekly", re.IGNORECASE)
_SUMMARY_KEYWORDS = re.compile(r"סיכום|summarize|summary|סכמי|תסכמי", re.IGNORECASE)


def _current_week_range():
    from datetime import timedelta
    today = date.today()
    weekday = today.isoweekday() % 7  # Sun=0 … Sat=6
    start = today - timedelta(days=weekday)
    return start, today


def _build_meal_context(meals: list, exercises: dict, steady_meals: list, recent_convos: list, fitbit_stats: dict | None = None) -> str:
    lines = []

    if meals:
        lines.append("Meals logged today (use meal IDs for edit/delete):")
        for m in meals:
            cal = f"{round(m['calories'])} kcal" if m.get("calories") else "?"
            prot = f", {round(m['protein_g'])}g protein" if m.get("protein_g") else ""
            cat = m.get("meal_category") or "?"
            lines.append(f"  - [ID:{m['id']}] {m['meal_name']} ({cat}): {cal}{prot}")
    else:
        lines.append("No meals logged today yet.")

    if exercises and exercises.get("items"):
        lines.append("\nExercise today (use exercise IDs for delete):")
        for ex in exercises["items"]:
            eid = ex.get("id", "?")
            act = ex.get("activity", "?")
            dur = f"{ex.get('duration_min', '?')} min" if ex.get("duration_min") else ""
            cal = f", {ex.get('calories', '?')} kcal" if ex.get("calories") else ""
            lines.append(f"  - [ID:{eid}] {act}: {dur}{cal}")

    if steady_meals:
        lines.append("\nSteady meals / ארוחות קבועות (use IDs for delete):")
        for sm in steady_meals:
            cal = round(sm.get("calories") or 0)
            lines.append(f"  - [ID:{sm['id']}] {sm['product_name']} ({cal} kcal)")

    if fitbit_stats:
        parts = []
        if fitbit_stats.get("steps"):
            parts.append(f"Steps: {fitbit_stats['steps']:,}")
        if fitbit_stats.get("distance_km"):
            parts.append(f"Distance: {fitbit_stats['distance_km']} km")
        if fitbit_stats.get("activity_calories"):
            parts.append(f"Active calories: {fitbit_stats['activity_calories']}")
        if fitbit_stats.get("resting_hr"):
            parts.append(f"Resting HR: {fitbit_stats['resting_hr']} bpm")
        if fitbit_stats.get("sleep_minutes"):
            h, m = divmod(fitbit_stats["sleep_minutes"], 60)
            deep = fitbit_stats.get("sleep_deep_min") or 0
            rem = fitbit_stats.get("sleep_rem_min") or 0
            eff = fitbit_stats.get("sleep_efficiency")
            sleep_str = f"Sleep: {h}h {m}m (deep {deep}m, REM {rem}m"
            if eff:
                sleep_str += f", efficiency {eff}%"
            sleep_str += ")"
            parts.append(sleep_str)
        if parts:
            lines.append("\nFitbit today: " + " | ".join(parts))

    if recent_convos:
        lines.append("\nRecent conversation:")
        for conv in reversed(recent_convos):
            lines.append(f"  User: {conv['message_text']}")
            if conv.get("response_text"):
                lines.append(f"  Bot: {conv['response_text'][:200]}")

    return "\n".join(lines)


def _make_tool_executor(today):
    """Create a tool executor closure that dispatches tool calls to query functions."""

    def executor(tool_name: str, tool_input: dict):
        try:
            if tool_name == "add_meal":
                meal_data = {
                    "meal_name": tool_input["meal_name"],
                    "meal_category": tool_input["meal_category"],
                    "meal_date": today,
                    "meal_time": datetime.now().time(),
                    "source": "tool_use",
                    "confidence_score": tool_input.get("confidence_score", 0.9),
                }
                if tool_input.get("meal_date"):
                    meal_data["meal_date"] = date.fromisoformat(tool_input["meal_date"])
                for field in ["calories", "protein_g", "carbs_g", "fat_g", "fiber_g",
                              "sugar_g", "calcium_mg", "iron_mg", "magnesium_mg"]:
                    if tool_input.get(field) is not None:
                        meal_data[field] = tool_input[field]
                meal = add_meal_log(meal_data)
                return {"success": True, "meal_id": meal.id, "meal_name": meal.meal_name}

            elif tool_name == "update_meal":
                meal_id = tool_input.pop("meal_id")
                updates = {k: v for k, v in tool_input.items() if v is not None}
                success = update_meal_log(meal_id, updates)
                return {"success": success}

            elif tool_name == "delete_meal":
                success = delete_meal_log(tool_input["meal_id"])
                return {"success": success}

            elif tool_name == "lookup_food":
                result = asyncio.run(_lookup_food(tool_input["food_name"]))
                if result:
                    return {
                        "found": True,
                        "meal_name": result.get("meal_name", tool_input["food_name"]),
                        "calories": result.get("calories"),
                        "protein_g": result.get("protein_g"),
                        "carbs_g": result.get("carbs_g"),
                        "fat_g": result.get("fat_g"),
                        "fiber_g": result.get("fiber_g"),
                        "sugar_g": result.get("sugar_g"),
                        "calcium_mg": result.get("calcium_mg"),
                        "iron_mg": result.get("iron_mg"),
                        "magnesium_mg": result.get("magnesium_mg"),
                        "serving_size_g": result.get("serving_size_g"),
                        "confidence_score": result.get("confidence_score", 0.7),
                        "source": result.get("source", "unknown"),
                    }
                return {"found": False}

            elif tool_name == "add_exercise":
                from datetime import time as time_type
                ex_date = date.fromisoformat(tool_input["exercise_date"]) if tool_input.get("exercise_date") else today
                ex_time = None
                if tool_input.get("exercise_time"):
                    parts = tool_input["exercise_time"].split(":")
                    ex_time = time_type(int(parts[0]), int(parts[1]))
                success = insert_exercise(
                    exercise_date=ex_date,
                    exercise_time=ex_time,
                    activity=tool_input["activity"],
                    duration_min=tool_input.get("duration_min"),
                    calories=tool_input.get("calories"),
                    source="manual",
                )
                return {"success": success}

            elif tool_name == "delete_exercise":
                success = delete_exercise(tool_input["exercise_id"])
                return {"success": success}

            elif tool_name == "delete_steady_meal":
                success = delete_food_db_item(tool_input["steady_meal_id"])
                return {"success": success}

            elif tool_name == "search_food_db_item":
                items = find_food_db_items(tool_input["name"])
                return {"items": items, "count": len(items)}

            elif tool_name == "update_food_db_item":
                item_id = tool_input.pop("item_id")
                updates = {k: v for k, v in tool_input.items()}
                success = update_food_db_item(item_id, updates)
                return {"success": success}

            return {"error": f"Unknown tool: {tool_name}"}
        except Exception as e:
            logger.error("Tool executor error (%s): %s", tool_name, e)
            return {"error": str(e)}

    return executor


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
        await _silent_fitbit_sync()
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
        await _silent_fitbit_sync()
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

    try:
        exercises = get_exercise_for_date(today)
    except Exception as e:
        logger.error("get_exercise_for_date failed: %s", e)
        exercises = {}

    try:
        steady_meals = get_steady_meals()
    except Exception as e:
        logger.error("get_steady_meals failed: %s", e)
        steady_meals = []

    try:
        fitbit_stats = get_fitbit_stats_for_date(today)
    except Exception as e:
        logger.error("get_fitbit_stats_for_date failed: %s", e)
        fitbit_stats = None

    meal_context = _build_meal_context(meals, exercises, steady_meals, recent_convos, fitbit_stats)
    tool_executor = _make_tool_executor(today)

    try:
        answer = await answer_with_tools(question, meal_context, tool_executor)
    except Exception as e:
        logger.error("answer_with_tools failed: %s", e)
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
