import logging
from datetime import date, datetime

from telegram import Update
from telegram.ext import ContextTypes

from config import ALLOWED_TELEGRAM_USER_ID
from database.queries import add_food_db_item, add_meal_log, insert_exercise
from ai.claude_client import analyze_food_image
from bot.utils.keyboards import confirm_keyboard, confirm_with_edit_keyboard, category_keyboard, save_to_db_keyboard
from bot.utils.time_category import category_from_time

logger = logging.getLogger(__name__)

PENDING_LABEL_KEY = "pending_label"
PENDING_DISH_KEY = "pending_dish"

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


def _scale_label_to_grams(label: dict, new_grams: float) -> dict:
    import copy
    label = copy.deepcopy(label)
    per_100g = label.get("per_100g")
    old_serving_g = label.get("serving_size_g") or 0

    if per_100g:
        scale = new_grams / 100.0
        new_ps = {k: round(per_100g[k] * scale, 2) if per_100g.get(k) is not None else None
                  for k in NUTRIENT_KEYS}
    elif old_serving_g > 0:
        scale = new_grams / old_serving_g
        old_ps = label.get("per_serving") or {}
        new_ps = {k: round(old_ps[k] * scale, 2) if old_ps.get(k) is not None else None
                  for k in NUTRIENT_KEYS}
    else:
        return label

    label["per_serving"] = new_ps
    label["serving_size_g"] = new_grams
    return label


def _scale_dish_to_grams(dish: dict, new_grams: float) -> dict:
    import copy
    dish = copy.deepcopy(dish)
    old_g = dish.get("estimated_serving_g") or 0
    if old_g > 0:
        ratio = new_grams / old_g
        nutrition = dish.get("nutrition") or {}
        dish["nutrition"] = {
            k: round(v * ratio, 2) if v is not None else None
            for k, v in nutrition.items()
        }
    dish["estimated_serving_g"] = new_grams
    return dish


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


_DISH_FIELDS = [
    ("calories", "קלוריות", "קל'"),
    ("protein_g", "חלבון", "g"),
    ("carbs_g", "פחמימות", "g"),
    ("fat_g", "שומן", "g"),
    ("fiber_g", "סיבים", "g"),
    ("sugar_g", "סוכר", "g"),
    ("calcium_mg", "סידן", "mg"),
    ("iron_mg", "ברזל", "mg"),
]


def _render_dish_summary(dish: dict) -> str:
    lines = []
    name = dish.get("dish_name") or "מנה"
    lines.append(f"🍽 {name}")
    components = dish.get("components") or []
    if components:
        lines.append("מרכיבים: " + ", ".join(components))
    serving = dish.get("estimated_serving_g")
    if serving:
        lines.append(f"מנה משוערת: {serving}g")

    nutrition = dish.get("nutrition") or {}
    nut_lines = []
    for key, he, unit in _DISH_FIELDS:
        v = nutrition.get(key)
        if v is not None:
            nut_lines.append(f"  {he}: {v}{unit}")
    if nut_lines:
        lines.append("\nערכים תזונתיים (משוערים):")
        lines += nut_lines

    notes = dish.get("confidence_notes")
    if notes:
        lines.append(f"\nהערה: {notes}")

    lines.append("\n⚠️ הערכים משוערים בלבד.")
    return "\n".join(lines)


def _parse_hhmm(value):
    if not value:
        return None
    try:
        return datetime.strptime(value.strip(), "%H:%M").time()
    except (ValueError, AttributeError):
        return None


async def _handle_exercise_screenshot(update: Update, result: dict) -> None:
    items = result.get("items") or []
    if not items:
        await update.message.reply_text("לא זיהיתי פעילויות חדשות בצילום המסך. 🤔")
        return

    today = date.today()
    inserted_rows: list[dict] = []
    skipped = 0

    for it in items:
        activity = (it.get("activity") or "").strip()
        if not activity:
            continue
        t = _parse_hhmm(it.get("time"))
        duration = it.get("duration_min")
        calories = it.get("calories")
        try:
            ok = insert_exercise(today, t, activity, duration, calories)
        except Exception as e:
            logger.error("insert_exercise failed for %s: %s", activity, e)
            continue
        if ok:
            inserted_rows.append({"time": t, "activity": activity, "duration_min": duration, "calories": calories})
        else:
            skipped += 1

    if not inserted_rows and skipped:
        await update.message.reply_text("כבר רשמתי את הפעילויות האלה היום ✓")
        return
    if not inserted_rows:
        await update.message.reply_text("לא הצלחתי לרשום פעילויות מהתמונה.")
        return

    lines = [f"רשמתי {len(inserted_rows)} פעילויות להיום:"]
    total_min = 0
    total_kcal = 0
    for r in inserted_rows:
        time_str = r["time"].strftime("%H:%M") if r["time"] else "—"
        parts = [f"• {time_str} — {r['activity']}"]
        if r["duration_min"] is not None:
            parts.append(f"{r['duration_min']} דק׳")
            total_min += r["duration_min"]
        if r["calories"] is not None:
            parts.append(f"{r['calories']} קק״ל")
            total_kcal += r["calories"]
        lines.append(", ".join(parts))
    lines.append("")
    lines.append(f"סה״כ היום: {total_min} דק׳, {total_kcal} קק״ל פעילות 💪")
    if skipped:
        lines.append(f"({skipped} כבר היו רשומות)")
    await update.message.reply_text("\n".join(lines))


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_user or update.effective_user.id != ALLOWED_TELEGRAM_USER_ID:
        return

    # Clear any stale photo state from a previous abandoned flow
    context.user_data.pop(PENDING_LABEL_KEY, None)
    context.user_data.pop(PENDING_DISH_KEY, None)
    context.user_data.pop("awaiting_product_name", None)

    photos = update.message.photo
    if not photos:
        await update.message.reply_text("לא קיבלתי תמונה.")
        return

    largest_photo = max(photos, key=lambda p: p.file_size or 0)
    photo_file = await largest_photo.get_file()
    image_bytes = await photo_file.download_as_bytearray()

    user_caption = (update.message.caption or "").strip()[:255]

    await update.message.reply_text("מנתחת את התמונה... 🔍")

    try:
        result = await analyze_food_image(bytes(image_bytes), caption=user_caption, media_type="image/jpeg")
    except Exception as e:
        logger.exception("analyze_food_image raised")
        await update.message.reply_text(
            f"השרת עמוס כרגע, נסי שוב בעוד רגע 🙏\n\nשגיאה: {type(e).__name__}: {e}"
        )
        return

    if "error" in result:
        await update.message.reply_text(
            f"לא הצלחתי לנתח את התמונה.\n{result.get('error', '')}"
        )
        return

    image_type = result.get("image_type")

    if image_type == "exercise":
        await _handle_exercise_screenshot(update, result)
        return

    if image_type == "label":
        label_data = result
        if user_caption:
            label_data["product_name"] = user_caption
        product_name = label_data.get("product_name", "") or ""

        summary = _render_summary(label_data)
        context.user_data[PENDING_LABEL_KEY] = label_data

        if product_name:
            keyboard = confirm_with_edit_keyboard("label_confirm_yes", "label_edit", "label_confirm_no")
            await update.message.reply_text(
                f"{summary}\n\nלרשום את {product_name} כארוחה להיום?",
                reply_markup=keyboard,
            )
        else:
            context.user_data["awaiting_product_name"] = True
            await update.message.reply_text(
                f"{summary}\n\nלא הצלחתי לזהות את שם המוצר מהתווית.\nאיך לקרוא למוצר הזה?"
            )
        return

    if image_type == "dish":
        dish_data = result
        if user_caption:
            dish_data["dish_name"] = user_caption
        context.user_data[PENDING_DISH_KEY] = dish_data
        summary = _render_dish_summary(dish_data)
        keyboard = confirm_with_edit_keyboard("dish_confirm_yes", "dish_edit", "dish_confirm_no")
        await update.message.reply_text(
            f"{summary}\n\nלרשום את זה כארוחה להיום?",
            reply_markup=keyboard,
        )
        return

    # image_type == "other" or unknown
    reason = result.get("reason") or ""
    msg = "לא זיהיתי תווית או מנה בתמונה. נסי שוב? 📷"
    if reason:
        msg += f"\n({reason})"
    await update.message.reply_text(msg)


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

    keyboard = confirm_with_edit_keyboard("label_confirm_yes", "label_edit", "label_confirm_no")
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

        await query.edit_message_text(f"✓ {product_name} נרשם ל{category}.\n\n{_render_summary(label_data)}")

    elif data == "label_edit":
        label_data = context.user_data.get(PENDING_LABEL_KEY, {})
        serving_g = label_data.get("serving_size_g")
        per_serving = label_data.get("per_serving") or {}
        cal = per_serving.get("calories")
        hint_parts = []
        if serving_g:
            hint_parts.append(f"מנה נוכחית: {serving_g}g")
        if cal is not None:
            hint_parts.append(f"קלוריות: {round(cal)} קל'")
        hint = " | ".join(hint_parts)
        context.user_data["awaiting_edit_grams"] = True
        context.user_data["edit_context"] = {"flow": "label"}
        await query.edit_message_text(
            (f"{hint}\n\n" if hint else "") + "כמה גרם אכלת? (כתבי מספר)"
        )

    elif data == "dish_edit":
        dish = context.user_data.get(PENDING_DISH_KEY, {})
        old_g = dish.get("estimated_serving_g")
        nutrition = dish.get("nutrition") or {}
        cal = nutrition.get("calories")
        hint_parts = []
        if old_g:
            hint_parts.append(f"מנה משוערת: {old_g}g")
        if cal is not None:
            hint_parts.append(f"קלוריות: {round(cal)} קל'")
        hint = " | ".join(hint_parts)
        context.user_data["awaiting_edit_grams"] = True
        context.user_data["edit_context"] = {"flow": "dish"}
        await query.edit_message_text(
            (f"{hint}\n\n" if hint else "") + "כמה גרם אכלת? (כתבי מספר)"
        )

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

    elif data == "dish_confirm_yes":
        dish = context.user_data.pop(PENDING_DISH_KEY, {})
        if not dish:
            await query.edit_message_text("לא נמצאו נתונים לשמירה.")
            return
        category = category_from_time()
        nutrition = dish.get("nutrition") or {}
        dish_name = dish.get("dish_name") or "מנה"
        meal_entry = {
            "meal_name": dish_name,
            "meal_category": category,
            "meal_date": date.today(),
            "meal_time": datetime.now().time(),
            "source": "claude_estimated",
            "confidence_score": 0.7,
            "food_db_item_id": None,
            **{k: nutrition.get(k) for k in MEAL_LOG_KEYS},
        }
        try:
            add_meal_log(meal_entry)
        except Exception as e:
            logger.error("add_meal_log (dish) failed: %s", e)

        await query.edit_message_text(f"✓ {dish_name} נרשם ל{category}.\n\n{_render_dish_summary(dish)}")

    elif data == "dish_confirm_no":
        context.user_data.pop(PENDING_DISH_KEY, None)
        await query.edit_message_text("בסדר, לא נרשם.")
