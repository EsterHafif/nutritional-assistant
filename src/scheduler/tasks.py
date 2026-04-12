import logging
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
import pytz

logger = logging.getLogger(__name__)


def setup_scheduler(bot) -> AsyncIOScheduler:
    tz = pytz.timezone("Asia/Jerusalem")
    scheduler = AsyncIOScheduler(timezone=tz)

    scheduler.add_job(
        morning_reminder,
        CronTrigger(hour=9, minute=0, timezone=tz),
        args=[bot],
        id="morning_reminder",
    )
    scheduler.add_job(
        evening_summary,
        CronTrigger(hour=21, minute=0, timezone=tz),
        args=[bot],
        id="evening_summary",
    )
    scheduler.add_job(
        weekly_summary,
        CronTrigger(day_of_week="sat", hour=21, minute=1, timezone=tz),
        args=[bot],
        id="weekly_summary",
    )
    scheduler.add_job(
        fitbit_sync_job,
        CronTrigger(hour=20, minute=55, timezone=tz),
        args=[bot],
        id="fitbit_sync",
    )
    scheduler.start()
    logger.info("Scheduler started: morning reminder at 09:00, evening summary at 21:00, weekly summary Sat 21:01, fitbit sync at 20:55 (Asia/Jerusalem)")
    return scheduler


async def fitbit_sync_job(bot) -> None:
    from datetime import date
    import asyncio
    from external_apis.fitbit import sync_fitbit_activities
    try:
        await asyncio.to_thread(sync_fitbit_activities, date.today())
        logger.info("fitbit_sync_job completed")
    except Exception as e:
        logger.error("fitbit_sync_job failed: %s", e)


async def weekly_summary(bot) -> None:
    from datetime import date, timedelta
    from config import ALLOWED_TELEGRAM_USER_ID
    from database.queries import get_weekly_data
    from ai.claude_client import generate_weekly_summary

    today = date.today()  # Saturday
    weekday = today.isoweekday() % 7  # Sat → 6
    week_start = today - timedelta(days=weekday)  # last Sunday
    week_end = today

    try:
        weekly_data = get_weekly_data(week_start, week_end)
    except Exception as e:
        logger.error("weekly_summary: DB error: %s", e)
        return

    try:
        summary_text = await generate_weekly_summary(weekly_data, week_start, week_end, is_partial=False)
    except Exception as e:
        logger.error("weekly_summary: generate_weekly_summary failed: %s", e)
        totals = weekly_data.get("totals", {})
        summary_text = (
            f"סיכום שבועי:\n"
            f"קלוריות: {round(totals.get('calories', 0))} קל׳\n"
            f"חלבון: {round(totals.get('protein_g', 0))}גר׳\n"
            f"ימים מתועדים: {weekly_data.get('days_fully_logged', 0)}/7"
        )

    try:
        await bot.send_message(chat_id=ALLOWED_TELEGRAM_USER_ID, text=summary_text)
    except Exception as e:
        logger.error("weekly_summary: send_message failed: %s", e)


async def morning_reminder(bot) -> None:
    from datetime import date, timedelta
    from config import ALLOWED_TELEGRAM_USER_ID, REQUIRED_MEAL_CATEGORIES
    from database.queries import get_logged_categories_for_date

    yesterday = date.today() - timedelta(days=1)
    try:
        logged = get_logged_categories_for_date(yesterday)
    except Exception as e:
        logger.error("morning_reminder: DB error: %s", e)
        return

    if all(cat in logged for cat in REQUIRED_MEAL_CATEGORIES):
        return

    try:
        await bot.send_message(
            chat_id=ALLOWED_TELEGRAM_USER_ID,
            text=(
                "בוקר טוב! 🌅\n\n"
                "שכחת לרשום את הארוחות של אתמול?\n\n"
                "שלחי לי את היומן בפורמט:\n\n"
                "בוקר:\n...\n\nצהריים:\n...\n\nערב:\n..."
            ),
        )
    except Exception as e:
        logger.error("morning_reminder: send_message failed: %s", e)


async def evening_summary(bot) -> None:
    from datetime import date
    from config import ALLOWED_TELEGRAM_USER_ID, REQUIRED_MEAL_CATEGORIES, MEAL_CATEGORIES
    from database.queries import get_logged_categories_for_date, get_daily_totals, get_meals_for_date
    from ai.claude_client import generate_daily_summary

    today = date.today()
    try:
        logged = get_logged_categories_for_date(today)
    except Exception as e:
        logger.error("evening_summary: DB error fetching categories: %s", e)
        return

    if not all(cat in logged for cat in REQUIRED_MEAL_CATEGORIES):
        return

    try:
        totals = get_daily_totals(today)
        all_meals = get_meals_for_date(today)
    except Exception as e:
        logger.error("evening_summary: DB error fetching totals: %s", e)
        return

    # Group meals by category in canonical order
    meals_by_category: dict = {}
    for cat in MEAL_CATEGORIES:
        items = [
            {
                "meal_name": m["meal_name"],
                "calories": m["calories"],
                "protein_g": m["protein_g"],
            }
            for m in all_meals if m.get("meal_category") == cat
        ]
        if items:
            meals_by_category[cat] = items

    try:
        summary_text = await generate_daily_summary(totals, meals_by_category)
    except Exception as e:
        logger.error("evening_summary: generate_daily_summary failed: %s", e)
        summary_text = (
            f"סיכום יומי:\n"
            f"קלוריות: {round(totals.get('calories', 0))} קל'\n"
            f"חלבון: {round(totals.get('protein_g', 0))}g\n"
            f"פחמימות: {round(totals.get('carbs_g', 0))}g\n"
            f"שומן: {round(totals.get('fat_g', 0))}g"
        )

    try:
        await bot.send_message(chat_id=ALLOWED_TELEGRAM_USER_ID, text=summary_text)
    except Exception as e:
        logger.error("evening_summary: send_message failed: %s", e)
