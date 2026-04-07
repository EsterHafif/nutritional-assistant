def progress_bar(value: float, target: float, width: int = 10) -> str:
    if target <= 0:
        return ""
    pct = min(value / target, 1.0)
    filled = round(pct * width)
    bar = "█" * filled + "░" * (width - filled)
    return f"[{bar}] {round(pct * 100)}%"


def format_meal_logged(category: str, items: list[dict], lang: str) -> str:
    if not items:
        return ""
    total_cal = sum(i.get("calories") or 0 for i in items)
    total_prot = sum(i.get("protein_g") or 0 for i in items)
    names = ", ".join(i.get("meal_name", "") for i in items)
    estimated = any(i.get("estimated") or i.get("source") == "claude_estimated" for i in items)

    if lang == "he":
        flag = " (משוער)" if estimated else ""
        return f"✓ {category}: {names}{flag}\n{round(total_cal)} קל' | {round(total_prot)}g חלבון"
    else:
        flag = " (estimated)" if estimated else ""
        return f"✓ {category}: {names}{flag}\n{round(total_cal)} kcal | {round(total_prot)}g protein"


def format_daily_totals(totals: dict, lang: str) -> str:
    cal = round(totals.get("calories", 0))
    prot = round(totals.get("protein_g", 0))
    bar = progress_bar(cal, 1500)
    if lang == "he":
        return f"\nסה\"כ היום: {cal} קל' {bar} | חלבון: {prot}g"
    return f"\nToday total: {cal} kcal {bar} | Protein: {prot}g"
