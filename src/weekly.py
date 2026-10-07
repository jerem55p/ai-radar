"""Récap du dimanche : top 10 de la semaine, projets qui se confirment, feux de paille."""
from __future__ import annotations

from collections import Counter
from datetime import date, timedelta

CONFIRM_DAYS = 4          # présent au moins 4 jours sur 7
MIN_HISTORY_DAYS = 5      # en dessous, on ne classe pas « confirmés / feux de paille »


def build_weekly(state, today: str, cfg: dict) -> dict:
    d0 = date.fromisoformat(today)
    window = [(d0 - timedelta(days=i)).isoformat() for i in range(6, -1, -1)]
    known_days = {d for e in state.repos.values() for d in e.get("ranks", {}) if d in window}
    enough = len(known_days) >= MIN_HISTORY_DAYS
    rows = []
    for fn, e in state.repos.items():
        days_in_top = [d for d in e.get("top20", []) if d in window]
        if not days_in_top:
            continue
        daily_sum = sum(v for d, v in e.get("daily", {}).items() if d in window)
        last7 = [v for d, v in sorted(e.get("stars7d", {}).items()) if d in window]
        gain = max(daily_sum, last7[-1] if last7 else 0)
        peak = max([v for d, v in e.get("daily", {}).items() if d in window] or [0])
        meta = e.get("meta", {})
        rows.append({"full_name": fn, "html_url": meta.get("html_url") or f"https://github.com/{fn}",
                     "category": e.get("category") or cfg["default_category"], "one_liner": e.get("one_liner", ""),
                     "gain_7d": gain, "days_in_top": len(days_in_top), "peak": peak, "owner": fn.split("/")[0]})
    rows.sort(key=lambda r: -r["gain_7d"])
    confirmed = [r for r in rows if r["days_in_top"] >= CONFIRM_DAYS] if enough else []
    flashes = [r for r in rows if r["days_in_top"] == 1 and r["peak"] >= 0.5 * max(r["gain_7d"], 1)] if enough else []
    cat_gain: Counter = Counter()
    for r in rows:
        cat_gain[r["category"]] += r["gain_7d"]
    top_cat = None
    if cat_gain:
        cid, gain = cat_gain.most_common(1)[0]
        top_cat = {"id": cid, "gain": gain}
    return {"start": window[0], "top10": rows[:10], "confirmed": confirmed, "flashes": flashes,
            "top_category": top_cat, "enough_history": enough}
