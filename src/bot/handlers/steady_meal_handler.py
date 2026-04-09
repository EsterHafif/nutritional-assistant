import logging
import re
from telegram import Update
from telegram.ext import ContextTypes
from config import ALLOWED_TELEGRAM_USER_ID
from ai.claude_client import parse_meal_text
from database.queries import add_food_db_item, search_food_db, search_food_db_candidates
from external_apis import lookup_food
from bot.utils.keyboards import steady_meal_save_keyboard, steady_meal_fuzzy_keyboard, confirm_with_edit_keyboard, edit_items_keyboard

logger = logging.getLogger(__name__)

PENDING_STEADY_MEAL_KEY = "pending_steady_meal"
FUZZY_QUEUE_KEY = "steady_fuzzy_queue"
TRIGGER = "ארוחה קבועה"

NUTRIENT_FIELDS = [
    "calories", "protein_g", "carbs_g", "fat_g", "fiber_g",
    "sugar_g", "calcium_mg", "magnesium_mg", "iron_mg",
]

# Same patterns as external_apis._extract_quantity, used here to find the food-name
# portion of a component string so we can substitute the candidate name and re-look-up.
_QUANTITY_PREFIX_RE = re.compile(
    r'^(\d+(?:\.\d+)?)\s*(?:גרם|ג׳|g|gr|מל|מ"ל|ml|כוס)?\s+',
    re.IGNORECASE,
)


def is_steady_meal_creation(text: str) -> bool:
    return text.strip().startswith(TRIGGER)


def _extract_description(text: str) -> str:
    text = text.strip()
    for sep in [":", "-"]:
        if TRIGGER + sep in text:
            return text[text.index(TRIGGER + sep) + len(TRIGGER) + 1:].strip()
    return text[len(TRIGGER):].strip()


_LEADING_CONNECTORS = ("של ", "עם ", "את ")


def _clean_component(c: str) -> str:
    """Strip leading Hebrew connectors like 'של ' / 'עם ' that confuse food lookup."""
    c = c.strip()
    changed = True
    while changed:
        changed = False
        for conn in _LEADING_CONNECTORS:
            if c.startswith(conn):
                c = c[len(conn):].strip()
                changed = True
                break
    return c


def _split_components(description: str) -> list[str]:
    # First split by newlines / commas
    raw = [c.strip() for c in description.replace("\r", "").split("\n") if c.strip()]
    if len(raw) <= 1:
        raw = [c.strip() for c in description.split(",") if c.strip()]
    # Then further split each piece on " עם " (with) — coffee with milk → 2 components
    expanded: list[str] = []
    for piece in raw:
        for sub in re.split(r"\s+עם\s+", piece):
            cleaned = _clean_component(sub)
            if cleaned:
                expanded.append(cleaned)
    return expanded


def _split_quantity_prefix(comp: str) -> tuple[str, str]:
    """Split 'X גרם FOOD' → ('X גרם ', 'FOOD'). Returns ('', comp) if no quantity."""
    m = _QUANTITY_PREFIX_RE.match(comp)
    if m:
        prefix = comp[:m.end()]
        rest = comp[m.end():].strip()
        return prefix, rest
    return "", comp


def _aggregate_nutrition(items: list[dict]) -> dict:
    totals: dict = {f: None for f in NUTRIENT_FIELDS}
    for item in items:
        for f in NUTRIENT_FIELDS:
            val = item.get(f)
            if val is not None:
                totals[f] = (totals[f] or 0) + val
    return totals


def _scale_steady_item(item: dict, new_grams: float) -> dict:
    import copy
    item = copy.deepcopy(item)
    values_per = item.get("values_per", "per_serving")
    serving_size_g = item.get("serving_size_g") or 0

    if values_per == "per_100g":
        scale = new_grams / 100.0
        base = item
    elif serving_size_g > 0:
        scale = new_grams / serving_size_g
        base = item
    else:
        return item

    for f in NUTRIENT_FIELDS:
        v = base.get(f)
        if v is not None:
            item[f] = round(v * scale, 2)
    item["serving_size_g"] = new_grams
    return item


def _format_nutrition_summary(data: dict) -> str:
    lines = []
    if data.get("calories") is not None:
        lines.append(f"קלוריות: {round(data['calories'])} קל'")
    if data.get("protein_g") is not None:
        lines.append(f"חלבון: {round(data['protein_g'], 1)}g")
    if data.get("carbs_g") is not None:
        lines.append(f"פחמימות: {round(data['carbs_g'], 1)}g")
    if data.get("fat_g") is not None:
        lines.append(f"שומן: {round(data['fat_g'], 1)}g")
    if data.get("fiber_g") is not None:
        lines.append(f"סיבים: {round(data['fiber_g'], 1)}g")
    if data.get("calcium_mg") is not None:
        lines.append(f"סידן: {round(data['calcium_mg'])}mg")
    return "\n".join(lines)


def _format_item_short(item: dict) -> str:
    parts = []
    if item.get("calories") is not None:
        parts.append(f"{round(item['calories'])} קל'")
    if item.get("protein_g") is not None:
        parts.append(f"{round(item['protein_g'], 1)}g חלבון")
    return " | ".join(parts)


def _format_breakdown(items: list[dict], totals: dict) -> str:
    lines = ["רכיבים:"]
    for it in items:
        name = it.get("meal_name") or it.get("product_name") or "?"
        marker = " (אומדן)" if (it.get("source") == "claude_estimated") else ""
        short = _format_item_short(it)
        lines.append(f"• {name}{marker}: {short}" if short else f"• {name}{marker}")
    lines.append("")
    lines.append("סה\"כ:")
    lines.append(_format_nutrition_summary(totals))
    return "\n".join(lines)


def _candidate_label(c) -> str:
    name = getattr(c, "product_name", None) or "?"
    brand = getattr(c, "brand", None)
    return f"{name} ({brand})" if brand else name


async def _resolve_components(components: list[str]) -> tuple[list[dict], list[dict]]:
    """For each component string, run lookup_food. Build a parallel `items` list and
    a `fuzzy_queue` list of components that fell back to Claude estimate but have
    DB candidates worth confirming."""
    items: list[dict] = []
    fuzzy_queue: list[dict] = []

    for idx, comp in enumerate(components):
        try:
            food_data = await lookup_food(comp, cache=False)
        except Exception as e:
            logger.error("lookup_food failed for %s: %s", comp, e)
            food_data = {}
        if not food_data:
            food_data = {"meal_name": comp, "source": "claude_estimated", "confidence_score": 0.7}
        food_data.setdefault("meal_name", comp)
        items.append(food_data)

        # If this fell through to Claude estimate, try a fuzzy DB search
        if food_data.get("source") == "claude_estimated":
            _, food_part = _split_quantity_prefix(comp)
            search_target = food_part or comp
            candidates = search_food_db_candidates(search_target, limit=3)
            if candidates:
                fuzzy_queue.append({
                    "idx": idx,
                    "original": comp,
                    "search_target": search_target,
                    "candidates": [
                        {"product_name": c.product_name, "brand": c.brand, "label": _candidate_label(c)}
                        for c in candidates
                    ],
                })
    return items, fuzzy_queue


async def handle_steady_meal_creation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_user or update.effective_user.id != ALLOWED_TELEGRAM_USER_ID:
        return

    context.user_data.pop(PENDING_STEADY_MEAL_KEY, None)
    context.user_data.pop(FUZZY_QUEUE_KEY, None)
    context.user_data.pop("awaiting_steady_meal_name", None)

    text = update.message.text or ""
    description = _extract_description(text)

    if not description:
        await update.message.reply_text(
            "כתבי את תיאור הארוחה הקבועה אחרי הנקודתיים, לדוגמה:\n"
            "ארוחה קבועה: קפה עם 90 מל חלב"
        )
        return

    await update.message.reply_text("מחשבת ערכים תזונתיים... ⏳")

    components = _split_components(description)
    items, fuzzy_queue = await _resolve_components(components)

    if not items:
        await update.message.reply_text(
            "לא הצלחתי לחשב ערכים תזונתיים לתיאור הזה. נסי לנסח מחדש."
        )
        return

    context.user_data[PENDING_STEADY_MEAL_KEY] = {
        "description": description,
        "components": components,
        "items": items,
    }

    if fuzzy_queue:
        context.user_data[FUZZY_QUEUE_KEY] = fuzzy_queue
        await _ask_next_fuzzy(update, context)
        return

    await _finalize_breakdown(update, context)


async def _ask_next_fuzzy(update_or_query, context: ContextTypes.DEFAULT_TYPE) -> None:
    queue = context.user_data.get(FUZZY_QUEUE_KEY) or []
    if not queue:
        return
    current = queue[0]
    cand = current["candidates"][0]
    msg = (
        f"לא מצאתי בדיוק את '{current['search_target']}' במאגר.\n"
        f"אולי התכוונת ל: {cand['label']}?"
    )
    kb = steady_meal_fuzzy_keyboard()
    await update_or_query.message.reply_text(msg, reply_markup=kb)


async def _finalize_breakdown(update_or_query, context: ContextTypes.DEFAULT_TYPE) -> None:
    pending = context.user_data.get(PENDING_STEADY_MEAL_KEY) or {}
    items = pending.get("items") or []
    totals = _aggregate_nutrition(items)
    pending["totals"] = totals
    context.user_data[PENDING_STEADY_MEAL_KEY] = pending

    breakdown = _format_breakdown(items, totals)
    msg = f"{breakdown}\n\nהנתונים נראים בסדר?"
    await update_or_query.message.reply_text(
        msg,
        reply_markup=confirm_with_edit_keyboard(
            "steady_breakdown_ok", "steady_breakdown_edit", "steady_breakdown_cancel"
        ),
    )


async def handle_steady_meal_name_reply(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    if not context.user_data.get("awaiting_steady_meal_name"):
        return False
    if not update.effective_user or update.effective_user.id != ALLOWED_TELEGRAM_USER_ID:
        return False
    if not context.user_data.get(PENDING_STEADY_MEAL_KEY):
        context.user_data.pop("awaiting_steady_meal_name", None)
        return False

    name = update.message.text.strip()[:255]
    context.user_data.pop("awaiting_steady_meal_name", None)

    pending = context.user_data[PENDING_STEADY_MEAL_KEY]
    pending["name"] = name

    existing = search_food_db(name)
    overwrite_note = ""
    if existing and getattr(existing, "source", None) == "steady_meal":
        pending["overwrite_id"] = getattr(existing, "id", None)
        overwrite_note = "\n⚠️ ארוחה קבועה בשם זה כבר קיימת — שמירה תדרוס אותה."

    items = pending.get("items") or []
    totals = pending.get("totals") or _aggregate_nutrition(items)
    breakdown = _format_breakdown(items, totals)
    await update.message.reply_text(
        f"לשמור את '{name}' כארוחה קבועה?{overwrite_note}\n\n{breakdown}",
        reply_markup=steady_meal_save_keyboard(),
    )
    return True


async def handle_steady_meal_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    query = update.callback_query
    if not query:
        return False
    data = query.data or ""
    _known = {"steady_save_yes", "steady_save_no", "steady_fuzzy_yes", "steady_fuzzy_no",
               "steady_breakdown_ok", "steady_breakdown_edit", "steady_breakdown_cancel"}
    if data not in _known and not data.startswith("steady_ei_"):
        return False

    await query.answer()

    if data in ("steady_fuzzy_yes", "steady_fuzzy_no"):
        return await _handle_fuzzy_callback(query, context)

    if data == "steady_breakdown_cancel":
        context.user_data.pop(PENDING_STEADY_MEAL_KEY, None)
        await query.edit_message_text("בסדר, לא נשמר.")
        return True

    if data == "steady_breakdown_ok":
        context.user_data["awaiting_steady_meal_name"] = True
        await query.edit_message_text("איך לקרוא לארוחה הקבועה הזו?")
        return True

    if data == "steady_breakdown_edit":
        pending = context.user_data.get(PENDING_STEADY_MEAL_KEY) or {}
        items = pending.get("items") or []
        await query.edit_message_text(
            "איזה רכיב לשנות?",
            reply_markup=edit_items_keyboard(items, "steady_ei_", "steady_breakdown_ok"),
        )
        return True

    if data.startswith("steady_ei_"):
        try:
            idx = int(data[len("steady_ei_"):])
        except ValueError:
            return True
        pending = context.user_data.get(PENDING_STEADY_MEAL_KEY) or {}
        items = pending.get("items") or []
        if idx >= len(items):
            await query.edit_message_text("פריט לא נמצא.")
            return True
        item = items[idx]
        name = item.get("meal_name") or f"רכיב {idx + 1}"
        serving_g = item.get("serving_size_g")
        cal = item.get("calories")
        hint_parts = []
        if serving_g:
            hint_parts.append(f"מנה נוכחית: {serving_g}g")
        if cal is not None:
            hint_parts.append(f"קלוריות: {round(cal)} קל'")
        hint = " | ".join(hint_parts)
        context.user_data["awaiting_edit_grams"] = True
        context.user_data["edit_context"] = {"flow": "steady", "item_idx": idx}
        await query.edit_message_text(
            f"{name}\n{hint}\n\nכמה גרם?".strip()
        )
        return True

    if data == "steady_save_no":
        context.user_data.pop(PENDING_STEADY_MEAL_KEY, None)
        await query.edit_message_text("בסדר, לא נשמר.")
        return True

    # steady_save_yes
    pending = context.user_data.pop(PENDING_STEADY_MEAL_KEY, {})
    if not pending:
        await query.edit_message_text("לא נמצאו נתונים לשמירה.")
        return True

    meal_name = pending.get("name", "ארוחה קבועה")
    totals = pending.get("totals") or _aggregate_nutrition(pending.get("items") or [])
    item_data = {
        "product_name": meal_name,
        "source": "steady_meal",
        "values_per": "per_serving",
        **{f: totals.get(f) for f in NUTRIENT_FIELDS},
        "data": {
            "description": pending.get("description"),
            "components": pending.get("items"),
        },
    }

    try:
        add_food_db_item(item_data)
        breakdown = _format_breakdown(pending.get("items") or [], totals)
        await query.edit_message_text(
            f"✓ '{meal_name}' נשמרה כארוחה קבועה 📌\n\n{breakdown}\n\nבפעם הבאה פשוט כתבי את השם!"
        )
    except Exception as e:
        logger.error("add_food_db_item steady_meal failed: %s", e)
        await query.edit_message_text("שגיאה בשמירה, נסי שוב.")

    return True


async def _handle_fuzzy_callback(query, context: ContextTypes.DEFAULT_TYPE) -> bool:
    queue = context.user_data.get(FUZZY_QUEUE_KEY) or []
    pending = context.user_data.get(PENDING_STEADY_MEAL_KEY) or {}
    if not queue or not pending:
        await query.edit_message_text("מצב הטופס אבד, נסי לשלוח את הארוחה שוב.")
        context.user_data.pop(FUZZY_QUEUE_KEY, None)
        context.user_data.pop(PENDING_STEADY_MEAL_KEY, None)
        return True

    current = queue[0]
    items = pending.get("items") or []

    if query.data == "steady_fuzzy_yes":
        cand = current["candidates"][0]
        prefix, _ = _split_quantity_prefix(current["original"])
        new_lookup = f"{prefix}{cand['product_name']}".strip()
        try:
            new_food = await lookup_food(new_lookup, cache=False)
        except Exception as e:
            logger.error("lookup_food (fuzzy confirm) failed for %s: %s", new_lookup, e)
            new_food = {}
        if new_food:
            new_food.setdefault("meal_name", current["original"])
            items[current["idx"]] = new_food
            pending["items"] = items
            context.user_data[PENDING_STEADY_MEAL_KEY] = pending
            await query.edit_message_text(f"✓ עודכן: {cand['label']}")
        else:
            await query.edit_message_text(
                f"לא הצלחתי למשוך את {cand['label']} מהמאגר, נשאר באומדן."
            )
    else:  # steady_fuzzy_no
        await query.edit_message_text(f"בסדר, נשאר באומדן עבור '{current['search_target']}'.")

    queue.pop(0)
    context.user_data[FUZZY_QUEUE_KEY] = queue

    if queue:
        await _ask_next_fuzzy(query, context)
    else:
        context.user_data.pop(FUZZY_QUEUE_KEY, None)
        await _finalize_breakdown(query, context)
    return True
