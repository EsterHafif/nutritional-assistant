import re
import asyncio
from database.queries import search_food_db, add_food_db_item
from external_apis.open_food_facts import search_off
from external_apis.usda_fdc import search_usda
from ai.claude_client import parse_meal_text

NUTRIENT_FIELDS = [
    "calories", "protein_g", "carbs_g", "fat_g", "fiber_g",
    "sugar_g", "calcium_mg", "magnesium_mg", "iron_mg",
]

QUANTITY_PATTERNS = [
    (r"^(\d+(?:\.\d+)?)\s*(?:גרם|ג׳|g|gr)\s+(.+)$", "g"),
    (r"^(\d+(?:\.\d+)?)\s*(?:מל|מ\"ל|ml)\s+(.+)$", "g"),
    (r"^(\d+(?:\.\d+)?)\s*(?:כוס)\s+(.+)$", "cup"),
    (r"^(\d+)\s+(.+)$", "units"),
]


def _food_db_item_to_dict(item) -> dict:
    fields = [
        "id", "product_name", "brand", "barcode", "calories", "protein_g", "carbs_g",
        "fat_g", "fiber_g", "sugar_g", "saturated_fat_g", "serving_size_g",
        "calcium_mg", "magnesium_mg", "iron_mg", "zinc_mg", "potassium_mg",
        "sodium_mg", "phosphorus_mg", "vitamin_a_mcg", "vitamin_c_mg",
        "vitamin_d_mcg", "vitamin_b12_mcg", "folate_mcg", "source", "source_id",
        "values_per",
    ]
    return {f: getattr(item, f, None) for f in fields}


def _extract_quantity(food_name: str) -> tuple[str, float | None]:
    for pattern, unit in QUANTITY_PATTERNS:
        m = re.match(pattern, food_name.strip(), re.IGNORECASE)
        if m:
            qty = float(m.group(1))
            term = m.group(2).strip()
            if unit == "cup":
                return term, qty * 240
            elif unit == "units":
                return term, None
            else:
                return term, qty
    return food_name, None


def _scale_to_quantity(result: dict, quantity_g: float | None) -> dict:
    if not quantity_g or result.get("values_per") != "per_100g":
        return result
    scale = quantity_g / 100
    for field in NUTRIENT_FIELDS:
        if result.get(field) is not None:
            result[field] = round(result[field] * scale, 1)
    result["serving_size_g"] = quantity_g
    result["values_per"] = "per_serving"
    return result


async def lookup_food(name: str) -> dict:
    # Step 0: steady meal
    db_item = search_food_db(name)
    if db_item and getattr(db_item, "source", None) == "steady_meal":
        result = _food_db_item_to_dict(db_item)
        result["confidence_score"] = 1.0
        result["meal_name"] = result.get("product_name", name)
        return result

    search_term, quantity_g = _extract_quantity(name)
    prefer = "per_100g" if quantity_g else "per_serving"

    # Step 1: food_db
    db_item = search_food_db(search_term, prefer=prefer)
    if db_item:
        result = _food_db_item_to_dict(db_item)
        result["confidence_score"] = 1.0
        result["meal_name"] = name
        return _scale_to_quantity(result, quantity_g)

    # Step 2: Open Food Facts
    off_data = await asyncio.to_thread(search_off, search_term)
    if off_data:
        off_data["meal_name"] = name
        add_food_db_item(off_data)
        off_data["confidence_score"] = 0.95
        return _scale_to_quantity(off_data, quantity_g)

    # Step 3: USDA
    usda_data = await asyncio.to_thread(search_usda, search_term)
    if usda_data:
        usda_data["meal_name"] = name
        add_food_db_item(usda_data)
        usda_data["confidence_score"] = 0.95
        return _scale_to_quantity(usda_data, quantity_g)

    # Step 4: Claude estimate
    parsed = await parse_meal_text(name)
    if parsed:
        item = parsed[0]
        if not item.get("meal_name"):
            item["meal_name"] = name
        item["source"] = "claude_estimated"
        item["confidence_score"] = 0.7
        return item

    return {}
