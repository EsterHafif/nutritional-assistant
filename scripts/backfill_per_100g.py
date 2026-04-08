"""One-shot backfill: for every per_serving food_db_items row that has a serving_size_g,
create a paired per_100g row if one does not already exist. Idempotent."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from dotenv import load_dotenv  # noqa: E402
load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

from database.db import get_session  # noqa: E402
from database.models import FoodDBItem  # noqa: E402
from bot.handlers.photo_handler import NUTRIENT_KEYS, _derive_per_100g  # noqa: E402


def main() -> None:
    with get_session() as s:
        rows = s.query(FoodDBItem).filter(
            FoodDBItem.values_per == "per_serving",
            FoodDBItem.serving_size_g.isnot(None),
            FoodDBItem.serving_size_g > 0,
        ).all()

        created = 0
        skipped = 0
        for r in rows:
            brand_filter = (
                FoodDBItem.brand.is_(None) if r.brand is None else FoodDBItem.brand == r.brand
            )
            exists = s.query(FoodDBItem.id).filter(
                FoodDBItem.product_name == r.product_name,
                brand_filter,
                FoodDBItem.values_per == "per_100g",
            ).first()
            if exists:
                skipped += 1
                continue

            per_serving = {k: getattr(r, k, None) for k in NUTRIENT_KEYS}
            per_100g = _derive_per_100g(per_serving, r.serving_size_g)
            if not per_100g:
                skipped += 1
                continue

            new = FoodDBItem(
                product_name=r.product_name,
                brand=r.brand,
                serving_size_g=r.serving_size_g,
                values_per="per_100g",
                source=(r.source or "") + "_backfill",
                **per_100g,
            )
            s.add(new)
            created += 1

        print(f"Created {created}, skipped {skipped}")


if __name__ == "__main__":
    main()
