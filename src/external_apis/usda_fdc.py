from typing import Optional
import httpx

USDA_SEARCH_URL = "https://api.nal.usda.gov/fdc/v1/foods/search"

NUTRIENT_ID_MAP = {
    1008: "calories",
    1003: "protein_g",
    1005: "carbs_g",
    1004: "fat_g",
    1079: "fiber_g",
    2000: "sugar_g",
    1258: "saturated_fat_g",
    1087: "calcium_mg",
    1090: "magnesium_mg",
    1089: "iron_mg",
    1095: "zinc_mg",
    1092: "potassium_mg",
    1093: "sodium_mg",
    1091: "phosphorus_mg",
    1106: "vitamin_a_mcg",
    1162: "vitamin_c_mg",
    1114: "vitamin_d_mcg",
    1178: "vitamin_b12_mcg",
    1177: "folate_mcg",
}


def search_usda(name: str) -> Optional[dict]:
    from config import USDA_API_KEY
    if not USDA_API_KEY:
        return None
    try:
        params = {
            "query": name,
            "api_key": USDA_API_KEY,
            "pageSize": 5,
            "dataType": "Foundation,SR Legacy,Branded",
        }
        with httpx.Client(timeout=10.0) as client:
            resp = client.get(USDA_SEARCH_URL, params=params)
            resp.raise_for_status()
            data = resp.json()

        foods = data.get("foods", [])
        if not foods:
            return None

        food = foods[0]
        nutrients_raw = food.get("foodNutrients", [])

        nutrients: dict = {}
        for n in nutrients_raw:
            nutrient_id = n.get("nutrientId")
            value = n.get("value")
            if nutrient_id in NUTRIENT_ID_MAP and value is not None:
                try:
                    nutrients[NUTRIENT_ID_MAP[nutrient_id]] = float(value)
                except (TypeError, ValueError):
                    pass

        if not nutrients:
            return None

        result = {
            "product_name": food.get("description", name),
            "brand": food.get("brandOwner", ""),
            "source": "usda_fdc",
            "source_id": str(food.get("fdcId", "")),
            "serving_size_g": food.get("servingSize"),
        }
        result.update(nutrients)
        result["values_per"] = "per_100g"
        result["data"] = food
        return result
    except Exception:
        return None
