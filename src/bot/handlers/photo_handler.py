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

NUTRIENT_KEYS = [
    "calories", "protein_g", "carbs_g", "fat_g", "fiber_g", "sugar_g",
    "saturated_fat_g", "calcium_mg", "magnesium_mg", "iron_mg",
    "sodium_mg", "potassium_mg",
]

MEAL_LOG_KEYS = [
    "calories", "protein_g", "carbs_g", "fat_g", "fiber_g",
    "sugar_g", "calcium_mg", "magnesium_mg", "iron_mg",
]


def _derive_per_100g(per_serving: dict | None, serving_size_g: float | None) -> dict | None:
    if not per_serving or not serving_size_g or serving_size_g <= 0:
        return None
    factor = 100.0 / serving_size_g
    out = {}
    for k in NUTRIENT_KEYS:
        v = per_serving.get(k)
        out[k] = round(v * factor, 2) if v is not None else None
    return out


def _build_food_row(label: dict, values: dict, values_per: str) -> dict:
    return {
        "product_name": label.get("product_name"),
        "brand": label.get("brand"),
        "serving_size_g": label.get("serving_size_g"),
        "values_per": values_per,
        "source": "label_photo",
        **{k: values.get(k) for k in NUTRIENT_KEYS},
    }


def _save_label_rows(label: dict) -> int | None:
    """Insert per_serving and per_100g rows. Returns the per_serving row id (or per_100g if no per_serving)."""
    per_serving = label.get("per_serving")
    per_100g = label.get("per_100g")
    serving_size_g = label.get("serving_size_g")

    if per_100g is None:
        per_100g = _derive_per_100g(per_serving, serving_size_g)

    primary_id = None
    if per_serving:
        row = _build_food_row(label, per_serving, "per_serving")
        try:
            saved = add_food_db_item(row)
            primary_id = saved.id
        except Exception as e:
            logger.error("add_food_db_item per_serving failed: %s", e)
    if per_100g:
        row = _build_food_row(label, per_100g, "per_100g")
        try:
            saved = add_food_db_item(row)
            if primary_id is None:
                primary_id = saved.id
        except Exception as e:
            logger.error("add_food_db_item per_100g failed: %s", e)
    return primary_id


_DISPLAY_FIELDS = [
    ("calories", "קלוריות", "קל'"),
    ("protein_g", "חלבון", "g"),
    ("carbs_g", "פחמימות", "g"),
    ("fat_g", "שומן", "g"),
    ("fiber_g", "סיבים", "g"),
    ("calcium_mg", "סידן", "mg"),
    ("iron_mg", "ברזל", "mg"),
]


def _render_summary(label: dict) -> str:
    lines = []
    if label.get("product_name"):
        lines.append(f"מוצר: {label['product_name']}")
    if label.get("brand"):
        lines.append(f"מותג: {label['brand']}")
    if label.get("serving_size_g"):
        lines.append(f"מנה: {label['serving_size_g']}g")

    per_serving = label.get("per_serving")
    per_100g = label.get("per_100g") or _derive_per_100g(per_serving, label.get("serving_size_g"))

    def fmt(values, header):
        if not values:
            return []
        out = [f"\n{header}:"]
        any_val = False
        for key, he, unit in _DISPLAY_FIELDS:
            v = values.get(key)
            if v is not None:
                out.append(f"  {he}: {v}{unit}")
                any_val = True
        return out if any_val else []

    lines += fmt(per_serving, "למנה")
    lines += fmt(per_100g, "ל-100g")

    unreadable = label.get("unreadable_fields") or []
    if unreadable:
        lines.append(f"\nשדות שלא נקראו בבירור: {', '.join(unreadable)}")

    return "\n".join(lines) if lines else "לא נמצאו ערכים תזונתיים ברורים."


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_user or update.effective_user.id != ALLOWED_TELEGRAM_USER_ID:
        return

    # Clear any stale photo state from a previous abandoned flow
    context.user_data.pop(PENDING_LABEL_KEY, None)
    context.user_data.pop("awaiting_product_name", None)

    photos = update.message.photo
    if not photos:
        await update.message.reply_text("לא קיבלתי תמונה.")
        return

    largest_photo = max(photos, key=lambda p: p.file_size or 0)
    photo_file = await largest_photo.get_file()
    image_bytes = await photo_file.download_as_bytearray()

    user_caption = (update.message.caption or "").strip()[:255]

    await update.message.reply_text("מנתח את התווית... 🔍")

    try:
        label_data = await extract_label_from_image(bytes(image_bytes), media_type="image/jpeg")
    except Exception as e:
        logger.exception("extract_label_from_image raised")
        await update.message.reply_text(
            f"התווית עמוסה כרגע, נסי שוב בעוד רגע 🙏\n\nשגיאה: {type(e).__name__}: {e}"
        )
        return

    if "error" in label_data:
        await update.message.reply_text(
            f"לא הצלחתי לקרוא את התווית.\n{label_data.get('error', '')}"
        )
        return

    # User's caption takes priority over label-detected name
    if user_caption:
        label_data["product_name"] = user_caption
    product_name = label_data.get("product_name", "") or ""

    summary = _render_summary(label_data)

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
    if not context.user_data.get(PENDING_LABEL_KEY):
        # Label data was lost (e.g. bot restarted) — clear stale state
        context.user_data.pop("awaiting_product_name", None)
        return False

    product_name = update.message.text.strip()[:255]
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
        primary_id = _save_label_rows(label_data)

        per_serving = label_data.get("per_serving") or {}
        meal_entry = {
            "meal_name": product_name,
            "meal_category": category,
            "meal_date": date.today(),
            "meal_time": datetime.now().time(),
            "source": "label_photo",
            "confidence_score": 1.0,
            "food_db_item_id": primary_id,
            **{k: per_serving.get(k) for k in MEAL_LOG_KEYS},
        }

        try:
            add_meal_log(meal_entry)
        except Exception as e:
            logger.error("add_meal_log failed in callback: %s", e)

        cal = per_serving.get("calories")
        prot = per_serving.get("protein_g")
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
        primary_id = _save_label_rows(label_data)
        if primary_id is None:
            await query.edit_message_text("שגיאה בשמירה למאגר.")
            return

        await query.edit_message_text(
            f"✓ {product_name} נשמר במאגר המזון 📦\n\n{_render_summary(label_data)}"
        )

    elif data == "db_save_no":
        context.user_data.pop(PENDING_LABEL_KEY, None)
        await query.edit_message_text("בסדר, לא נשמר.")
