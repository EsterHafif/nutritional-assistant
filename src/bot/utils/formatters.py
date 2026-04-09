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

    if lang == "he":
        lines = [f"✓ {category}:"]
        for i in items:
            name = i.get("meal_name", "")
            cal = i.get("calories")
            prot = i.get("protein_g")
            estimated = i.get("estimated") or i.get("source") == "claude_estimated"
            marker = " (משוער)" if estimated else ""
            parts = []
            if cal is not None:
                parts.append(f"{round(cal)} קל'")
            if prot is not None:
                parts.append(f"{round(prot, 1)}g חלבון")
            nutrition = " | ".join(parts)
            lines.append(f"• {name}{marker}: {nutrition}" if nutrition else f"• {name}{marker}")
        lines.append(f"סה\"כ: {round(total_cal)} קל' | {round(total_prot, 1)}g חלבון")
        return "\n".join(lines)
    else:
        lines = [f"✓ {category}:"]
        for i in items:
            name = i.get("meal_name", "")
            cal = i.get("calories")
            prot = i.get("protein_g")
            estimated = i.get("estimated") or i.get("source") == "claude_estimated"
            marker = " (estimated)" if estimated else ""
            parts = []
            if cal is not None:
                parts.append(f"{round(cal)} kcal")
            if prot is not None:
                parts.append(f"{round(prot, 1)}g protein")
            nutrition = " | ".join(parts)
            lines.append(f"• {name}{marker}: {nutrition}" if nutrition else f"• {name}{marker}")
        lines.append(f"Total: {round(total_cal)} kcal | {round(total_prot, 1)}g protein")
        return "\n".join(lines)


def format_daily_totals(totals: dict, lang: str) -> str:
    cal = round(totals.get("calories", 0))
    prot = round(totals.get("protein_g", 0))
    bar = progress_bar(cal, 1500)
    if lang == "he":
        return f"\nסה\"כ היום: {cal} קל' {bar} | חלבון: {prot}g"
    return f"\nToday total: {cal} kcal {bar} | Protein: {prot}g"
