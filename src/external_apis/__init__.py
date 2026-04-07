from typing import Optional
from database.queries import search_food_db, add_food_db_item
from external_apis.open_food_facts import search_off
from external_apis.usda_fdc import search_usda
from ai.claude_client import parse_meal_text


def _food_db_item_to_dict(item) -> dict:
    fields = [
        "id", "product_name", "brand", "barcode", "calories", "protein_g", "carbs_g",
        "fat_g", "fiber_g", "sugar_g", "saturated_fat_g", "serving_size_g",
        "calcium_mg", "magnesium_mg", "iron_mg", "zinc_mg", "potassium_mg",
        "sodium_mg", "phosphorus_mg", "vitamin_a_mcg", "vitamin_c_mg",
        "vitamin_d_mcg", "vitamin_b12_mcg", "folate_mcg", "source", "source_id",
    ]
    return {f: getattr(item, f, None) for f in fields}


async def lookup_food(name: str) -> dict:
    db_item = search_food_db(name)
    if db_item:
        result = _food_db_item_to_dict(db_item)
        result["confidence_score"] = 1.0
        result["meal_name"] = result.get("product_name", name)
        return result

    off_data = search_off(name)
    if off_data:
        off_data["meal_name"] = off_data.get("product_name", name)
        add_food_db_item(off_data)
        off_data["confidence_score"] = 0.95
        return off_data

    usda_data = search_usda(name)
    if usda_data:
        usda_data["meal_name"] = usda_data.get("product_name", name)
        add_food_db_item(usda_data)
        usda_data["confidence_score"] = 0.95
        return usda_data

    parsed = await parse_meal_text(name)
    if parsed:
        item = parsed[0]
        if not item.get("meal_name"):
            item["meal_name"] = name
        item["source"] = "claude_estimated"
        item["confidence_score"] = 0.7
        return item

    return {}
