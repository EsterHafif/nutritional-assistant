from telegram import Update
from telegram.ext import ContextTypes
from config import ALLOWED_TELEGRAM_USER_ID


async def handle_unknown(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_user or update.effective_user.id != ALLOWED_TELEGRAM_USER_ID:
        return
    await update.message.reply_text(
        "שלום! אני יכול לעזור לך עם:\n"
        "• לרשום ארוחות — שלחי יומן ארוחות בפורמט בוקר/צהריים/ערב\n"
        "• לנתח תוויות — שלחי תמונה של תווית מזון\n"
        "• לענות על שאלות — 'מה אכלתי היום?' / 'כמה חלבון צרכתי?'"
    )
