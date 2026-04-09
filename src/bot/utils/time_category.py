from datetime import datetime
from zoneinfo import ZoneInfo

from config import TIMEZONE

TZ = ZoneInfo(TIMEZONE)


def category_from_time(now: datetime | None = None) -> str:
    h = (now or datetime.now(TZ)).hour
    if 6 <= h < 10:
        return "בוקר"
    if 10 <= h < 12:
        return "ביניים"
    if 12 <= h < 16:
        return "צהריים"
    if 16 <= h < 18:
        return "אחר הצהריים"
    if 18 <= h < 21:
        return "ערב"
    return "בוקר" if h < 6 else "ערב"
