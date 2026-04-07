import logging
from datetime import date, datetime

from telegram import Update
from telegram.ext import ContextTypes

from config import ALLOWED_TELEGRAM_USER_ID
from database.queries import add_food_db_item, add_meal_log
from ai.claude_client import extract_label_from_image
from bot.utils.keyboards import confirm_keyboard, category_keyboard, save_to_db_keyboard

logger = logging.getLogger(__name__)

PENDING_LABEL_KEY = "pending_label"


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_user or update.effective_user.id != ALLOWED_TELEGRAM_USER_ID:
        return

    photos = update.message.photo
    if not photos:
        await update.message.reply_text("לא קיבלתי תמונה.")
        return

    largest_photo = max(photos, key=lambda p: p.file_size or 0)
    photo_file = await largest_photo.get_file()
    image_bytes = await photo_file.download_as_bytearray()

    user_caption = (update.message.caption or "").strip()

    await update.message.reply_text("מנתח את התווית... 🔍")

    label_data = await extract_label_from_image(bytes(image_bytes), media_type="image/jpeg")

    if "error" in label_data:
        await update.message.reply_text(
            f"לא הצלחתי לקרוא את התווית.\n{label_data.get('error', '')}"
        )
        return

    unreadable = label_data.pop("unreadable_fields", [])

    lines = []
    # User's caption takes priority over label-detected name
    if user_caption:
        label_data["product_name"] = user_caption
    product_name = label_data.get("product_name", "") or ""
    if product_name:
        lines.append(f"מוצר: {product_name}")
    if label_data.get("brand"):
        lines.append(f"מותג: {label_data['brand']}")
    if label_data.get("serving_size_g"):
        lines.append(f"מנה: {label_data['serving_size_g']}g")
    if label_data.get("calories") is not None:
        lines.append(f"קלוריות: {label_data['calories']} קל'")
    if label_data.get("protein_g") is not None:
        lines.append(f"חלבון: {label_data['protein_g']}g")
    if label_data.get("carbs_g") is not None:
        lines.append(f"פחמימות: {label_data['carbs_g']}g")
    if label_data.get("fat_g") is not None:
        lines.append(f"שומן: {label_data['fat_g']}g")
    if label_data.get("fiber_g") is not None:
        lines.append(f"סיבים: {label_data['fiber_g']}g")
    if label_data.get("calcium_mg") is not None:
        lines.append(f"סידן: {label_data['calcium_mg']}mg")
    if label_data.get("iron_mg") is not None:
        lines.append(f"ברזל: {label_data['iron_mg']}mg")

    if unreadable:
        lines.append(f"\nשדות שלא נקראו בבירור: {', '.join(unreadable)}")

    summary = "\n".join(lines) if lines else "לא נמצאו ערכים תזונתיים ברורים."

    context.user_data[PENDING_LABEL_KEY] = label_data

    if product_name:
        keyboard = confirm_keyboard("label_confirm_yes", "label_confirm_no")
        await update.message.reply_text(
            f"{summary}\n\nלרשום את {product_name} כארוחה להיום?",
            reply_markup=keyboard,
        )
    else:
        context.user_data["awaiting_product_name"] = True
        await update.message.reply_text(
            f"{summary}\n\nלא הצלחתי לזהות את שם המוצר מהתווית.\nאיך לקרוא למוצר הזה?"
        )


async def handle_product_name_reply(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Call this from the text router. Returns True if it handled the message."""
    if not context.user_data.get("awaiting_product_name"):
        return False
    if not update.effective_user or update.effective_user.id != ALLOWED_TELEGRAM_USER_ID:
        return False

    product_name = update.message.text.strip()
    context.user_data.pop("awaiting_product_name", None)
    label_data: dict = context.user_data.get(PENDING_LABEL_KEY, {})
    label_data["product_name"] = product_name
    context.user_data[PENDING_LABEL_KEY] = label_data

    keyboard = confirm_keyboard("label_confirm_yes", "label_confirm_no")
    await update.message.reply_text(
        f"לרשום את {product_name} כארוחה להיום?",
        reply_markup=keyboard,
    )
    return True


async def handle_photo_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query:
        return
    if not update.effective_user or update.effective_user.id != ALLOWED_TELEGRAM_USER_ID:
        await query.answer()
        return

    await query.answer()
    data = query.data

    if data == "label_confirm_yes":
        label_data: dict = context.user_data.get(PENDING_LABEL_KEY, {})
        if not label_data:
            await query.edit_message_text("לא נמצאו נתונים לשמירה.")
            return
        product_name = label_data.get("product_name", "מוצר לא ידוע")
        await query.edit_message_text(
            f"באיזו ארוחה לרשום את {product_name}?",
            reply_markup=category_keyboard(),
        )

    elif data.startswith("cat_"):
        category = data[len("cat_"):]
        label_data = context.user_data.pop(PENDING_LABEL_KEY, {})
        if not label_data:
            await query.edit_message_text("לא נמצאו נתונים לשמירה.")
            return

        product_name = label_data.get("product_name", "מוצר לא ידוע")

        try:
            add_food_db_item(label_data)
        except Exception as e:
            logger.error("add_food_db_item failed in callback: %s", e)

        meal_entry = {
            "meal_name": product_name,
            "meal_category": category,
            "meal_date": date.today(),
            "meal_time": datetime.now().time(),
            "calories": label_data.get("calories"),
            "protein_g": label_data.get("protein_g"),
            "carbs_g": label_data.get("carbs_g"),
            "fat_g": label_data.get("fat_g"),
            "fiber_g": label_data.get("fiber_g"),
            "sugar_g": label_data.get("sugar_g"),
            "calcium_mg": label_data.get("calcium_mg"),
            "magnesium_mg": label_data.get("magnesium_mg"),
            "iron_mg": label_data.get("iron_mg"),
            "source": "label_photo",
            "confidence_score": 1.0,
        }

        try:
            add_meal_log(meal_entry)
        except Exception as e:
            logger.error("add_meal_log failed in callback: %s", e)

        cal = label_data.get("calories")
        prot = label_data.get("protein_g")
        details = []
        if cal is not None:
            details.append(f"{round(cal)} קל'")
        if prot is not None:
            details.append(f"{round(prot)}g חלבון")
        detail_str = " | ".join(details)
        await query.edit_message_text(f"✓ {product_name} נרשם ל{category}.\n{detail_str}")

    elif data == "label_confirm_no":
        label_data = context.user_data.get(PENDING_LABEL_KEY, {})
        product_name = label_data.get("product_name", "המוצר") if label_data else "המוצר"
        await query.edit_message_text(
            f"בסדר, לא נרשם כארוחה.\nרוצה שאשמור את {product_name} במאגר המזון לשימוש עתידי?",
            reply_markup=save_to_db_keyboard(),
        )

    elif data == "db_save_yes":
        label_data = context.user_data.pop(PENDING_LABEL_KEY, {})
        if not label_data:
            await query.edit_message_text("לא נמצאו נתונים לשמירה.")
            return
        product_name = label_data.get("product_name", "מוצר לא ידוע")
        try:
            add_food_db_item(label_data)
        except Exception as e:
            logger.error("add_food_db_item failed: %s", e)
            await query.edit_message_text("שגיאה בשמירה למאגר.")
            return

        lines = [f"✓ {product_name} נשמר במאגר המזון 📦"]
        if label_data.get("serving_size_g"):
            lines.append(f"מנה: {label_data['serving_size_g']}g")
        if label_data.get("calories") is not None:
            lines.append(f"קלוריות: {round(label_data['calories'])} קל'")
        if label_data.get("protein_g") is not None:
            lines.append(f"חלבון: {label_data['protein_g']}g")
        if label_data.get("carbs_g") is not None:
            lines.append(f"פחמימות: {label_data['carbs_g']}g")
        if label_data.get("fat_g") is not None:
            lines.append(f"שומן: {label_data['fat_g']}g")
        if label_data.get("fiber_g") is not None:
            lines.append(f"סיבים: {label_data['fiber_g']}g")
        if label_data.get("calcium_mg") is not None:
            lines.append(f"סידן: {label_data['calcium_mg']}mg")
        if label_data.get("sodium_mg") is not None:
            lines.append(f"נתרן: {label_data['sodium_mg']}mg")
        await query.edit_message_text("\n".join(lines))

    elif data == "db_save_no":
        context.user_data.pop(PENDING_LABEL_KEY, None)
        await query.edit_message_text("בסדר, לא נשמר.")
