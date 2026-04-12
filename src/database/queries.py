from datetime import date as date_type, datetime
from typing import Optional
from sqlalchemy import case, or_
from sqlalchemy.dialects.postgresql import insert as pg_insert
from .models import FoodDBItem, MealLog, ConversationHistory, ExerciseLog, FitbitDailyStats
from .db import get_session


_SEARCH_STOP_WORDS = {"של", "עם", "ב", "ה", "ו", "ל", "מ", "את", "על", "אל"}


def _significant_words(name: str) -> list[str]:
    return [w for w in name.split() if len(w) > 1 and w not in _SEARCH_STOP_WORDS]


def get_logged_categories_for_date(meal_date: date_type) -> list[str]:
    with get_session() as s:
        rows = s.query(MealLog.meal_category).filter(MealLog.meal_date == meal_date).all()
        return [r[0] for r in rows if r[0]]


def get_daily_totals(meal_date: date_type) -> dict:
    fields = ["calories", "protein_g", "carbs_g", "fat_g", "fiber_g", "sugar_g", "calcium_mg", "magnesium_mg", "iron_mg"]
    totals = {f: 0.0 for f in fields}
    with get_session() as s:
        rows = s.query(MealLog).filter(MealLog.meal_date == meal_date).all()
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


def search_food_db(name: str, prefer: Optional[str] = None) -> Optional[FoodDBItem]:
    with get_session() as s:
        q = s.query(FoodDBItem).filter(FoodDBItem.product_name.ilike(f"%{name}%"))
        if prefer:
            q = q.order_by(case((FoodDBItem.values_per == prefer, 0), else_=1))
        item = q.first()
        if item:
            s.expunge(item)
        return item


def search_food_db_candidates(name: str, limit: int = 3) -> list[FoodDBItem]:
    """Return up to `limit` rows that share at least one significant word with `name`.
    Used for fuzzy 'did you mean?' suggestions when an exact ILIKE substring miss occurs."""
    words = _significant_words(name)
    if not words:
        return []
    with get_session() as s:
        conditions = [FoodDBItem.product_name.ilike(f"%{w}%") for w in words]
        items = s.query(FoodDBItem).filter(or_(*conditions)).limit(limit * 4).all()
        seen: set = set()
        result: list[FoodDBItem] = []
        for item in items:
            key = (item.product_name, item.brand)
            if key in seen:
                continue
            seen.add(key)
            s.expunge(item)
            result.append(item)
            if len(result) >= limit:
                break
        return result


def add_food_db_item(item_data: dict) -> FoodDBItem:
    with get_session() as s:
        item = FoodDBItem(**{k: v for k, v in item_data.items() if hasattr(FoodDBItem, k)})
        s.add(item)
        s.flush()
        s.expunge(item)
        return item


def get_recent_conversation(limit: int = 5) -> list[dict]:
    with get_session() as s:
        rows = (
            s.query(ConversationHistory)
            .order_by(ConversationHistory.created_at.desc())
            .limit(limit)
            .all()
        )
        return [{"message_text": r.message_text, "response_text": r.response_text, "created_at": r.created_at} for r in rows]


def add_conversation_entry(message: str, response: str) -> None:
    with get_session() as s:
        s.add(ConversationHistory(message_text=message, response_text=response))
        # Keep only the last 200 entries
        subq = (
            s.query(ConversationHistory.id)
            .order_by(ConversationHistory.created_at.desc())
            .limit(200)
            .subquery()
        )
        s.query(ConversationHistory).filter(
            ConversationHistory.id.notin_(subq)
        ).delete(synchronize_session=False)


def get_steady_meals() -> list[dict]:
    fields = ["id", "product_name", "calories", "protein_g", "carbs_g", "fat_g",
              "fiber_g", "sugar_g", "calcium_mg", "magnesium_mg", "iron_mg",
              "serving_size_g", "values_per", "source"]
    with get_session() as s:
        rows = s.query(FoodDBItem).filter(FoodDBItem.source == "steady_meal").all()
        return [{f: getattr(r, f, None) for f in fields} for r in rows]


def insert_exercise(
    exercise_date: date_type,
    exercise_time,
    activity: str,
    duration_min: Optional[int],
    calories: Optional[int],
    source: str = "screenshot",
) -> bool:
    """Insert one exercise row. Returns True if inserted, False if duplicate (date+time+activity)."""
    with get_session() as s:
        stmt = pg_insert(ExerciseLog).values(
            exercise_date=exercise_date,
            exercise_time=exercise_time,
            activity=activity,
            duration_min=duration_min,
            calories=calories,
            source=source,
        ).on_conflict_do_nothing(index_elements=["exercise_date", "exercise_time", "activity"])
        result = s.execute(stmt)
        return (result.rowcount or 0) > 0


def get_exercise_for_date(target_date: date_type) -> dict:
    """Return today's exercise summary: total minutes, total kcal, and item list."""
    with get_session() as s:
        rows = (
            s.query(ExerciseLog)
            .filter(ExerciseLog.exercise_date == target_date)
            .order_by(ExerciseLog.exercise_time.asc().nullsfirst())
            .all()
        )
        items = [
            {
                "id": r.id,
                "time": r.exercise_time.strftime("%H:%M") if r.exercise_time else None,
                "activity": r.activity,
                "duration_min": r.duration_min,
                "calories": r.calories,
            }
            for r in rows
        ]
    total_min = sum((it["duration_min"] or 0) for it in items)
    total_kcal = sum((it["calories"] or 0) for it in items)
    return {"total_minutes": total_min, "total_kcal": total_kcal, "items": items}


def update_meal_log(meal_id: int, updates: dict) -> bool:
    with get_session() as s:
        meal = s.query(MealLog).filter(MealLog.id == meal_id).first()
        if not meal:
            return False
        for key, value in updates.items():
            if hasattr(MealLog, key) and key != "id":
                setattr(meal, key, value)
        return True


def delete_meal_log(meal_id: int) -> bool:
    with get_session() as s:
        meal = s.query(MealLog).filter(MealLog.id == meal_id).first()
        if not meal:
            return False
        s.delete(meal)
        return True


def delete_exercise(exercise_id: int) -> bool:
    with get_session() as s:
        row = s.query(ExerciseLog).filter(ExerciseLog.id == exercise_id).first()
        if not row:
            return False
        s.delete(row)
        return True


def update_exercise(exercise_id: int, updates: dict) -> bool:
    with get_session() as s:
        row = s.query(ExerciseLog).filter(ExerciseLog.id == exercise_id).first()
        if not row:
            return False
        for key, value in updates.items():
            if hasattr(ExerciseLog, key) and key != "id":
                setattr(row, key, value)
        return True


def delete_food_db_item(item_id: int) -> bool:
    with get_session() as s:
        item = s.query(FoodDBItem).filter(FoodDBItem.id == item_id).first()
        if not item:
            return False
        s.delete(item)
        return True


def upsert_fitbit_daily_stats(stat_date: date_type, data: dict) -> None:
    """Insert or update fitbit daily stats for a date."""
    with get_session() as s:
        stmt = pg_insert(FitbitDailyStats).values(stat_date=stat_date, **data)
        update_cols = {k: stmt.excluded[k] for k in data}
        update_cols["updated_at"] = datetime.utcnow()
        stmt = stmt.on_conflict_do_update(index_elements=["stat_date"], set_=update_cols)
        s.execute(stmt)


def get_fitbit_stats_for_date(target_date: date_type) -> dict | None:
    """Return fitbit daily stats dict for target_date, or None if not available."""
    fields = [
        "steps", "resting_hr", "activity_calories", "calories_out",
        "lightly_active_min", "fairly_active_min", "very_active_min",
        "sedentary_min", "distance_km", "sleep_minutes", "sleep_deep_min",
        "sleep_light_min", "sleep_rem_min", "sleep_efficiency",
        "sleep_start", "sleep_end",
    ]
    with get_session() as s:
        row = s.query(FitbitDailyStats).filter(FitbitDailyStats.stat_date == target_date).first()
        if not row:
            return None
        return {f: getattr(row, f, None) for f in fields}


def update_food_db_item(item_id: int, updates: dict) -> bool:
    with get_session() as s:
        item = s.query(FoodDBItem).filter(FoodDBItem.id == item_id).first()
        if not item:
            return False
        for key, value in updates.items():
            if hasattr(FoodDBItem, key) and key not in ("id", "created_at"):
                setattr(item, key, value)
        return True


def find_food_db_items(name: str, limit: int = 5) -> list[dict]:
    """Search food_db_items by name and return dicts with id and key fields."""
    fields = ["id", "product_name", "brand", "source", "calories", "protein_g",
              "carbs_g", "fat_g", "fiber_g", "sugar_g", "saturated_fat_g",
              "serving_size_g", "values_per",
              "calcium_mg", "magnesium_mg", "iron_mg", "zinc_mg",
              "potassium_mg", "sodium_mg", "phosphorus_mg",
              "vitamin_a_mcg", "vitamin_c_mg", "vitamin_d_mcg",
              "vitamin_b12_mcg", "folate_mcg"]
    with get_session() as s:
        rows = s.query(FoodDBItem).filter(
            FoodDBItem.product_name.ilike(f"%{name}%")
        ).limit(limit).all()
        return [{f: getattr(r, f, None) for f in fields} for r in rows]


def get_meals_for_date(meal_date: date_type) -> list[dict]:
    fields = ["id", "meal_name", "meal_category", "meal_date", "meal_time",
              "calories", "protein_g", "carbs_g", "fat_g", "fiber_g",
              "sugar_g", "calcium_mg", "magnesium_mg", "iron_mg", "source", "confidence_score"]
    with get_session() as s:
        rows = s.query(MealLog).filter(MealLog.meal_date == meal_date).all()
        return [{f: getattr(r, f, None) for f in fields} for r in rows]


_HE_DAY_NAMES = {0: "ראשון", 1: "שני", 2: "שלישי", 3: "רביעי", 4: "חמישי", 5: "שישי", 6: "שבת"}
_NUTRIENT_FIELDS = ["calories", "protein_g", "carbs_g", "fat_g", "fiber_g", "sugar_g", "calcium_mg", "magnesium_mg", "iron_mg"]


def get_weekly_data(start_date: date_type, end_date: date_type) -> dict:
    from datetime import timedelta
    days = []
    current = start_date
    while current <= end_date:
        totals = get_daily_totals(current)
        logged_cats = get_logged_categories_for_date(current)
        ex = get_exercise_for_date(current)
        fitbit = get_fitbit_stats_for_date(current)
        weekday = current.isoweekday() % 7  # Sun=0 … Sat=6
        days.append({
            "date": current.isoformat(),
            "day_name_he": _HE_DAY_NAMES[weekday],
            **totals,
            "logged_categories": logged_cats,
            "fully_logged": all(cat in logged_cats for cat in ["בוקר", "צהריים", "ערב"]),
            "exercise": {
                "total_minutes": ex.get("total_minutes") or 0,
                "total_kcal": ex.get("total_kcal") or 0,
                "activities": [it["activity"] for it in ex.get("items", []) if it.get("activity")],
            },
            "fitbit": {
                "steps": fitbit.get("steps") if fitbit else None,
                "sleep_minutes": fitbit.get("sleep_minutes") if fitbit else None,
                "sleep_deep_min": fitbit.get("sleep_deep_min") if fitbit else None,
                "sleep_rem_min": fitbit.get("sleep_rem_min") if fitbit else None,
                "resting_hr": fitbit.get("resting_hr") if fitbit else None,
                "activity_calories": fitbit.get("activity_calories") if fitbit else None,
            },
        })
        current += timedelta(days=1)

    days_with_data = [d for d in days if d.get("calories", 0) > 0]
    n = len(days_with_data) or 1

    weekly_totals = {f: round(sum(d.get(f) or 0 for d in days), 2) for f in _NUTRIENT_FIELDS}
    weekly_averages = {f: round(weekly_totals[f] / n, 2) for f in _NUTRIENT_FIELDS}

    exercise_days = [d for d in days if d["exercise"]["total_minutes"] > 0]
    weekly_exercise = {
        "sessions": len(exercise_days),
        "total_minutes": sum(d["exercise"]["total_minutes"] for d in days),
        "total_kcal": sum(d["exercise"]["total_kcal"] for d in days),
    }

    fitbit_days = [d for d in days if d["fitbit"].get("steps") is not None]
    n_fitbit = len(fitbit_days) or 1
    fitbit_weekly = {
        "avg_steps": round(sum(d["fitbit"]["steps"] or 0 for d in fitbit_days) / n_fitbit) if fitbit_days else None,
        "avg_sleep_minutes": round(sum(d["fitbit"]["sleep_minutes"] or 0 for d in fitbit_days) / n_fitbit) if fitbit_days else None,
        "avg_resting_hr": round(sum(d["fitbit"]["resting_hr"] or 0 for d in fitbit_days) / n_fitbit) if fitbit_days else None,
    }

    return {
        "days": days,
        "totals": weekly_totals,
        "averages": weekly_averages,
        "days_fully_logged": sum(1 for d in days if d["fully_logged"]),
        "days_with_any_data": len(days_with_data),
        "exercise": weekly_exercise,
        "fitbit_weekly": fitbit_weekly,
    }
