import logging
import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

from telegram import Update
from telegram.ext import Application, MessageHandler, CallbackQueryHandler, filters
from config import TELEGRAM_BOT_TOKEN, ALLOWED_TELEGRAM_USER_ID
from bot.handlers.meal_handler import handle_meal_log, is_structured_meal_log
from bot.handlers.photo_handler import handle_photo, handle_photo_callback, handle_product_name_reply
from bot.handlers.query_handler import handle_query
from bot.handlers.unknown_handler import handle_unknown
from scheduler.tasks import setup_scheduler

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)


async def route_text(update: Update, context) -> None:
    if not update.effective_user or update.effective_user.id != ALLOWED_TELEGRAM_USER_ID:
        return
    text = update.message.text or ""
    if await handle_product_name_reply(update, context):
        return
    if is_structured_meal_log(text):
        await handle_meal_log(update, context)
    else:
        await handle_query(update, context)


def main() -> None:
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, route_text))
    app.add_handler(CallbackQueryHandler(handle_photo_callback))

    async def on_startup(app):
        setup_scheduler(app.bot)

    app.post_init = on_startup
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
