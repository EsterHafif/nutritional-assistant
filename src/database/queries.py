from datetime import date as date_type
from typing import Optional
from .models import FoodDBItem, MealLog, ConversationHistory
from .db import get_session


def get_logged_categories_for_date(meal_date: date_type) -> list[str]:
    with get_session() as s:
        rows = s.query(MealLog.meal_category).filter(MealLog.meal_date == meal_date).all()
        return [r[0] for r in rows if r[0]]


def get_daily_totals(meal_date: date_type) -> dict:
    with get_session() as s:
        rows = s.query(MealLog).filter(MealLog.meal_date == meal_date).all()
    fields = ["calories", "protein_g", "carbs_g", "fat_g", "fiber_g", "sugar_g", "calcium_mg", "magnesium_mg", "iron_mg"]
    totals = {f: 0.0 for f in fields}
    for row in rows:
        for f in fields:
            val = getattr(row, f, None)
            if val:
                totals[f] += val
    return totals


def add_meal_log(meal_data: dict) -> MealLog:
    with get_session() as s:
        meal = MealLog(**meal_data)
        s.add(meal)
        s.flush()
        s.expunge(meal)
        return meal


def search_food_db(name: str) -> Optional[FoodDBItem]:
    with get_session() as s:
        item = s.query(FoodDBItem).filter(FoodDBItem.product_name.ilike(f"%{name}%")).first()
        if item:
            s.expunge(item)
        return item


def add_food_db_item(item_data: dict) -> FoodDBItem:
    with get_session() as s:
        item = FoodDBItem(**{k: v for k, v in item_data.items() if hasattr(FoodDBItem, k)})
        s.add(item)
        s.flush()
        s.expunge(item)
        return item


def get_recent_conversation(limit: int = 5) -> list:
    with get_session() as s:
        rows = (
            s.query(ConversationHistory)
            .order_by(ConversationHistory.created_at.desc())
            .limit(limit)
            .all()
        )
        for r in rows:
            s.expunge(r)
        return rows


def add_conversation_entry(message: str, response: str) -> None:
    with get_session() as s:
        s.add(ConversationHistory(message_text=message, response_text=response))


def get_meals_for_date(meal_date: date_type) -> list:
    with get_session() as s:
        rows = s.query(MealLog).filter(MealLog.meal_date == meal_date).all()
        for r in rows:
            s.expunge(r)
        return rows
