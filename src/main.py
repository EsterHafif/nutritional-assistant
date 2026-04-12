import logging
import re
import asyncio
import sys
import os
from datetime import date as _date
sys.path.insert(0, os.path.dirname(__file__))

from telegram import Update
from telegram.ext import Application, MessageHandler, CallbackQueryHandler, filters
from config import TELEGRAM_BOT_TOKEN, ALLOWED_TELEGRAM_USER_ID

_FITBIT_RE = re.compile(r"פיטביט|fitbit", re.IGNORECASE)
from bot.handlers.meal_handler import handle_meal_log, handle_meal_callback, is_structured_meal_log
from bot.handlers.photo_handler import handle_photo, handle_photo_callback, handle_product_name_reply
from bot.handlers.query_handler import handle_query
from bot.handlers.steady_meal_handler import (
    is_steady_meal_creation,
    handle_steady_meal_creation,
    handle_steady_meal_name_reply,
    handle_steady_meal_callback,
)
from bot.handlers.edit_handler import handle_edit_grams_reply
from bot.handlers.unknown_handler import handle_unknown
from scheduler.tasks import setup_scheduler

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)


async def handle_fitbit_sync(update: Update, context) -> None:
    from external_apis.fitbit import sync_fitbit_activities
    from database.queries import get_fitbit_stats_for_date
    await update.message.reply_text("מסנכרנת עם Fitbit... ⌚")
    result = await asyncio.to_thread(sync_fitbit_activities, _date.today())
    if result.get("error") == "no_tokens":
        await update.message.reply_text("לא מוגדר חיבור ל-Fitbit.")
        return
    if result.get("error"):
        await update.message.reply_text("שגיאה בסנכרון עם Fitbit. נסי שוב מאוחר יותר.")
        return

    lines = []

    # Workout activities
    inserted = result["inserted"]
    skipped = result["skipped"]
    items = result["items"]
    if inserted > 0:
        lines.append(f"סונכרנו {inserted} פעילויות:")
        for it in items:
            time_str = it["time"].strftime("%H:%M") if it.get("time") else "—"
            parts = [f"• {time_str} — {it['activity']}"]
            if it.get("duration_min"):
                parts.append(f"{it['duration_min']} דק׳")
            if it.get("calories"):
                parts.append(f"{it['calories']} קק״ל")
            lines.append(", ".join(parts))
        if skipped:
            lines.append(f"({skipped} כבר היו רשומות)")
    elif skipped:
        lines.append("פעילויות כבר רשומות ✓")
    else:
        lines.append("לא נמצאו אימונים להיום.")

    # Daily stats
    stats = get_fitbit_stats_for_date(_date.today())
    if stats:
        stat_parts = []
        if stats.get("steps"):
            stat_parts.append(f"צעדים: {stats['steps']:,}")
        if stats.get("distance_km"):
            stat_parts.append(f"מרחק: {stats['distance_km']} ק״מ")
        if stats.get("activity_calories"):
            stat_parts.append(f"קלוריות פעילות: {stats['activity_calories']}")
        if stats.get("resting_hr"):
            stat_parts.append(f"דופק מנוחה: {stats['resting_hr']} bpm")
        if stats.get("sleep_minutes"):
            h, m = divmod(stats["sleep_minutes"], 60)
            deep = stats.get("sleep_deep_min") or 0
            rem = stats.get("sleep_rem_min") or 0
            stat_parts.append(f"שינה: {h}ש׳ {m}דק׳ (עמוקה {deep}דק׳, REM {rem}דק׳)")
        if stat_parts:
            lines.append("\n" + " | ".join(stat_parts))

    await update.message.reply_text("\n".join(lines))


async def route_text(update: Update, context) -> None:
    if not update.effective_user or update.effective_user.id != ALLOWED_TELEGRAM_USER_ID:
        return
    text = update.message.text or ""
    if _FITBIT_RE.search(text):
        await handle_fitbit_sync(update, context)
        return
    if context.user_data.get("awaiting_edit_grams"):
        await handle_edit_grams_reply(update, context)
        return
    if await handle_product_name_reply(update, context):
        return
    if await handle_steady_meal_name_reply(update, context):
        return
    if is_steady_meal_creation(text):
        await handle_steady_meal_creation(update, context)
        return
    if is_structured_meal_log(text):
        await handle_meal_log(update, context)
    else:
        await handle_query(update, context)


async def route_callback(update: Update, context) -> None:
    if await handle_steady_meal_callback(update, context):
        return
    if await handle_meal_callback(update, context):
        return
    await handle_photo_callback(update, context)


def main() -> None:
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, route_text))
    app.add_handler(CallbackQueryHandler(route_callback))

    async def on_startup(app):
        setup_scheduler(app.bot)

    app.post_init = on_startup
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
