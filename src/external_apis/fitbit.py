import json
import logging
import os
from datetime import date, datetime, time as time_type

import httpx

logger = logging.getLogger(__name__)

_TOKENS_PATH = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "data", "fitbit_tokens.json")
)
_TOKEN_URL = "https://api.fitbit.com/oauth2/token"
_ACTIVITIES_URL = "https://api.fitbit.com/1/user/-/activities/date/{date}.json"
_SLEEP_URL = "https://api.fitbit.com/1/user/-/sleep/date/{date}.json"

_TRANSLATIONS = {
    "walk": "הליכה",
    "walking": "הליכה",
    "run": "ריצה",
    "running": "ריצה",
    "bike": "אופניים",
    "cycling": "אופניים",
    "elliptical": "אליפטיקל",
    "yoga": "יוגה",
    "swimming": "שחייה",
    "swim": "שחייה",
    "strength training": "אימון כוח",
    "weights": "משקולות",
    "treadmill": "הליכון",
    "sport": "אימון אירובי",
    "aerobic workout": "אימון אירובי",
    "hiit": "אימון אינטרוולים",
}


def load_tokens() -> dict | None:
    if not os.path.exists(_TOKENS_PATH):
        logger.warning("Fitbit tokens file not found: %s", _TOKENS_PATH)
        return None
    with open(_TOKENS_PATH) as f:
        return json.load(f)


def save_tokens(tokens: dict) -> None:
    os.makedirs(os.path.dirname(_TOKENS_PATH), exist_ok=True)
    with open(_TOKENS_PATH, "w") as f:
        json.dump(tokens, f, indent=2)


def refresh_access_token(tokens: dict) -> dict | None:
    import base64
    creds = base64.b64encode(
        f"{tokens['client_id']}:{tokens['client_secret']}".encode()
    ).decode()
    try:
        resp = httpx.post(
            _TOKEN_URL,
            headers={
                "Authorization": f"Basic {creds}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            data={"grant_type": "refresh_token", "refresh_token": tokens["refresh_token"]},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        tokens["access_token"] = data["access_token"]
        tokens["refresh_token"] = data["refresh_token"]
        save_tokens(tokens)
        logger.info("Fitbit token refreshed successfully")
        return tokens
    except Exception as e:
        logger.error("Fitbit token refresh failed: %s", e)
        return None


def _translate(name: str) -> str:
    return _TRANSLATIONS.get(name.lower(), name)


def _get_with_refresh(url: str, tokens: dict) -> httpx.Response | None:
    """GET a Fitbit URL, auto-refreshing on 401. Returns response or None on failure."""
    def _get(tok):
        return httpx.get(url, headers={"Authorization": f"Bearer {tok['access_token']}"}, timeout=10)

    resp = _get(tokens)
    if resp.status_code == 401:
        tokens = refresh_access_token(tokens)
        if not tokens:
            return None
        resp = _get(tokens)
    if not resp.is_success:
        logger.error("Fitbit request failed: %s %s", resp.status_code, resp.text[:200])
        return None
    return resp


def _fetch_activities_raw(target_date: date, tokens: dict) -> dict | None:
    """Fetch full activities response (workout list + summary stats)."""
    url = _ACTIVITIES_URL.format(date=target_date.isoformat())
    resp = _get_with_refresh(url, tokens)
    return resp.json() if resp else None


def _fetch_sleep(target_date: date, tokens: dict) -> dict | None:
    """Fetch sleep response for target_date."""
    url = _SLEEP_URL.format(date=target_date.isoformat())
    resp = _get_with_refresh(url, tokens)
    return resp.json() if resp else None


def sync_fitbit_all(target_date: date) -> dict:
    """Sync all Fitbit data for target_date:
    - Workout activities → exercise_log
    - Steps, HR, active calories, sleep → fitbit_daily_stats

    Returns {"inserted": int, "skipped": int, "items": list, "error": str|None}
    """
    from database.queries import insert_exercise, upsert_fitbit_daily_stats

    tokens = load_tokens()
    if not tokens:
        return {"inserted": 0, "skipped": 0, "items": [], "error": "no_tokens"}

    raw = _fetch_activities_raw(target_date, tokens)
    if raw is None:
        return {"inserted": 0, "skipped": 0, "items": [], "error": "api_error"}

    sleep_raw = _fetch_sleep(target_date, tokens)

    # --- Workout entries → exercise_log ---
    inserted, skipped, items = 0, 0, []
    for act in raw.get("activities", []):
        activity = _translate(act.get("activityName") or "")
        duration_min = round(act["duration"] / 60000) if act.get("duration") else None
        calories = act.get("calories") or None
        ex_time = None
        if act.get("startTime"):
            try:
                h, m = act["startTime"].split(":")
                ex_time = time_type(int(h), int(m))
            except (ValueError, AttributeError):
                pass
        try:
            ok = insert_exercise(
                exercise_date=target_date,
                exercise_time=ex_time,
                activity=activity,
                duration_min=duration_min,
                calories=calories,
                source="fitbit",
            )
        except Exception as e:
            logger.error("insert_exercise (fitbit) %s: %s", activity, e)
            continue
        if ok:
            inserted += 1
            items.append({"activity": activity, "duration_min": duration_min, "calories": calories, "time": ex_time})
        else:
            skipped += 1

    # --- Daily stats → fitbit_daily_stats ---
    stats: dict = {}
    summary = raw.get("summary", {})
    stats["steps"] = summary.get("steps")
    stats["resting_hr"] = summary.get("restingHeartRate")
    stats["activity_calories"] = summary.get("activityCalories")
    stats["calories_out"] = summary.get("caloriesOut")
    stats["lightly_active_min"] = summary.get("lightlyActiveMinutes")
    stats["fairly_active_min"] = summary.get("fairlyActiveMinutes")
    stats["very_active_min"] = summary.get("veryActiveMinutes")
    stats["sedentary_min"] = summary.get("sedentaryMinutes")
    distances = summary.get("distances") or []
    total_dist = next((d["distance"] for d in distances if d.get("activity") == "total"), None)
    stats["distance_km"] = round(total_dist, 3) if total_dist else None

    if sleep_raw:
        s = sleep_raw.get("summary", {})
        stats["sleep_minutes"] = s.get("totalMinutesAsleep")
        stages = s.get("stages") or {}
        stats["sleep_deep_min"] = stages.get("deep")
        stats["sleep_light_min"] = stages.get("light")
        stats["sleep_rem_min"] = stages.get("rem")
        main_sleep = next((sl for sl in sleep_raw.get("sleep", []) if sl.get("isMainSleep")), None)
        if main_sleep:
            stats["sleep_efficiency"] = main_sleep.get("efficiency")
            for field, key in [("sleep_start", "startTime"), ("sleep_end", "endTime")]:
                raw_val = main_sleep.get(key, "")
                try:
                    stats[field] = datetime.fromisoformat(raw_val).time()
                except (ValueError, TypeError):
                    stats[field] = None

    clean_stats = {k: v for k, v in stats.items() if v is not None}
    if clean_stats:
        try:
            upsert_fitbit_daily_stats(target_date, clean_stats)
        except Exception as e:
            logger.error("upsert_fitbit_daily_stats failed: %s", e)

    return {"inserted": inserted, "skipped": skipped, "items": items, "error": None}


# Backward-compatible alias
def sync_fitbit_activities(target_date: date) -> dict:
    return sync_fitbit_all(target_date)
