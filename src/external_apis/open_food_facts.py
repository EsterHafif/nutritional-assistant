from typing import Optional
import httpx

OFF_SEARCH_URL = "https://world.openfoodfacts.org/cgi/search.pl"


def _parse_nutriments(n: dict, serving_size_g: Optional[float]) -> dict:
    def get(key: str) -> Optional[float]:
        val = n.get(key)
        if val is None:
            return None
        try:
            return float(val)
        except (TypeError, ValueError):
            return None

    def scale(key: str, factor: float) -> Optional[float]:
        v = get(key)
        return v * factor if v is not None else None

    return {
        "calories": get("energy-kcal_100g"),
        "protein_g": get("proteins_100g"),
        "carbs_g": get("carbohydrates_100g"),
        "fat_g": get("fat_100g"),
        "fiber_g": get("fiber_100g"),
        "sugar_g": get("sugars_100g"),
        "saturated_fat_g": get("saturated-fat_100g"),
        "calcium_mg": scale("calcium_100g", 1000),
        "magnesium_mg": scale("magnesium_100g", 1000),
        "iron_mg": scale("iron_100g", 1000),
        "zinc_mg": scale("zinc_100g", 1000),
        "potassium_mg": scale("potassium_100g", 1000),
        "sodium_mg": scale("sodium_100g", 1000),
        "phosphorus_mg": scale("phosphorus_100g", 1000),
        "vitamin_a_mcg": scale("vitamin-a_100g", 1000000),
        "vitamin_c_mg": scale("vitamin-c_100g", 1000),
        "vitamin_d_mcg": scale("vitamin-d_100g", 1000000),
        "vitamin_b12_mcg": scale("vitamin-b12_100g", 1000000),
        "folate_mcg": scale("folate_100g", 1000000),
        "serving_size_g": serving_size_g,
    }


def search_off(name: str) -> Optional[dict]:
    try:
        params = {
            "search_terms": name,
            "search_simple": 1,
            "action": "process",
            "json": 1,
            "page_size": 5,
            "fields": "product_name,brands,nutriments,serving_size,code",
        }
        with httpx.Client(timeout=10.0) as client:
            resp = client.get(OFF_SEARCH_URL, params=params)
            resp.raise_for_status()
            data = resp.json()

        products = data.get("products", [])
        for product in products:
            nutriments = product.get("nutriments", {})
            if not nutriments:
                continue
            if not product.get("product_name"):
                continue

            serving_raw = product.get("serving_size", "")
            serving_size_g = None
            if serving_raw:
                import re
                match = re.search(r"(\d+(?:\.\d+)?)", serving_raw)
                if match:
                    try:
                        serving_size_g = float(match.group(1))
                    except ValueError:
                        pass

            result = {
                "product_name": product["product_name"],
                "brand": product.get("brands", ""),
                "barcode": product.get("code", ""),
                "source": "open_food_facts",
                "source_id": product.get("code", ""),
            }
            result.update(_parse_nutriments(nutriments, serving_size_g))
            return result

        return None
    except Exception:
        return None
