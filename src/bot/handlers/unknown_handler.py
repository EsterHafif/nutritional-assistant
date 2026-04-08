from telegram import Update
from telegram.ext import ContextTypes
from config import ALLOWED_TELEGRAM_USER_ID


async def handle_unknown(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_user or update.effective_user.id != ALLOWED_TELEGRAM_USER_ID:
        return
    await update.message.reply_text(
        "היי אסתר! אני פודי 🥑, העוזרת התזונתית האישית שלך.\n\n"
        "אני יכולה לעזור לך עם:\n"
        "• לרשום ארוחות — שלחי יומן בפורמט בוקר/צהריים/ערב\n"
        "• לנתח תוויות — שלחי תמונה של תווית מזון\n"
        "• לענות על שאלות — 'מה אכלתי היום?' / 'כמה חלבון צרכתי?'\n"
        "• ארוחות קבועות — שלחי 'ארוחה קבועה: קפה עם 90 מל חלב' לשמור תבנית"
    )
