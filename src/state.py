"""État persistant : data/history.json (versionné dans le dépôt)."""
from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path


def _d(s: str) -> date:
    return date.fromisoformat(s)


class State:
    """history.json :
    {"version": 1, "last_run": "AAAA-MM-JJ",
     "repos": {full_name: {"stars": {date: total}, "daily": {date: stars_24h}, "stars7d": {date: n},
                           "ranks": {date: rang}, "top20": [dates], "sent": [dates],
                           "category", "one_liner", "details", "meta": {...}}}}
    `ranks` : rang dans la liste étendue (top 20 final, puis le reste classé par score) pour le top 60.
    """

    def __init__(self, data: dict | None = None):
        self.data = data or {"version": 1, "repos": {}}
        self.data.setdefault("repos", {})

    # -- IO -------------------------------------------------------------------
    @classmethod
    def load(cls, path: Path) -> "State":
        try:
            return cls(json.loads(Path(path).read_text(encoding="utf-8")))
        except (FileNotFoundError, ValueError):
            return cls()

    def save(self, path: Path) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(json.dumps(self.data, ensure_ascii=False, indent=1, sort_keys=True),
                              encoding="utf-8")

    # -- accès ------------------------------------------------------------------
    @property
    def repos(self) -> dict:
        return self.data["repos"]

    @property
    def last_run(self) -> str | None:
        return self.data.get("last_run")

    def entry(self, full_name: str) -> dict:
        return self.repos.setdefault(full_name, {})

    def get(self, full_name: str) -> dict:
        return self.repos.get(full_name, {})

    # -- historique des étoiles ---------------------------------------------------
    def stars_diff(self, full_name: str, today: str, days: int, total_now: int) -> int | None:
        """Gain d'étoiles sur `days` jours par différence de snapshots (normalisé si l'écart diffère)."""
        stars = self.get(full_name).get("stars", {})
        if not stars:
            return None
        target = _d(today) - timedelta(days=days)
        best = None
        for ds, tot in stars.items():
            d = _d(ds)
            if d >= _d(today):
                continue
            gap = abs((d - target).days)
            if gap <= max(1, days // 3) and (best is None or gap < best[0]):
                best = (gap, d, tot)
        if best is None:
            return None
        elapsed = (_d(today) - best[1]).days
        if elapsed <= 0:
            return None
        gain = max(0, total_now - best[2])
        return round(gain * days / elapsed)

    def record(self, repo: dict, today: str) -> None:
        """Enregistre le snapshot du jour pour un dépôt mesuré."""
        e = self.entry(repo["full_name"])
        e.setdefault("stars", {})[today] = repo["stars"]
        if repo.get("stars_24h") is not None:
            e.setdefault("daily", {})[today] = repo["stars_24h"]
        if repo.get("stars_7d") is not None and repo.get("stars_7d_known", True):
            e.setdefault("stars7d", {})[today] = repo["stars_7d"]
        # reconstitution des jours manquants à partir des horodatages `starred_at`
        for ds, n in (repo.get("daily_backfill") or {}).items():
            if ds != today:
                e.setdefault("daily", {}).setdefault(ds, n)
        e["meta"] = {k: repo.get(k) for k in ("html_url", "description", "language", "license", "created_at",
                                              "pushed_at", "owner")}
        e["last_seen"] = today

    def save_texts(self, repo: dict) -> None:
        e = self.entry(repo["full_name"])
        for k in ("category", "one_liner", "details"):
            if repo.get(k):
                e[k] = repo[k]

    def record_ranks(self, extended: list[dict], top_n: int, today: str, keep: int = 60) -> None:
        for i, r in enumerate(extended[:keep], start=1):
            e = self.entry(r["full_name"])
            e.setdefault("ranks", {})[today] = i
            if i <= top_n:
                t = e.setdefault("top20", [])
                if today not in t:
                    t.append(today)

    def mark_sent(self, repos: list[dict], today: str) -> None:
        for r in repos:
            s = self.entry(r["full_name"]).setdefault("sent", [])
            if today not in s:
                s.append(today)

    # -- requêtes d'historique ---------------------------------------------------
    def previous_date(self, today: str) -> str | None:
        dates = {d for e in self.repos.values() for d in e.get("ranks", {}) if d < today}
        return max(dates) if dates else None

    def rank_on(self, full_name: str, day: str | None) -> int | None:
        return self.get(full_name).get("ranks", {}).get(day) if day else None

    def top20_before(self, full_name: str, today: str) -> list[str]:
        return sorted(d for d in self.get(full_name).get("top20", []) if d < today)

    def streak(self, full_name: str, today: str) -> int:
        """Nb de jours consécutifs dans le top 20, aujourd'hui compris (le dépôt y est aujourd'hui)."""
        days = set(self.top20_before(full_name, today))
        n, d = 1, _d(today) - timedelta(days=1)
        while d.isoformat() in days:
            n += 1
            d -= timedelta(days=1)
        return n

    def daily_series(self, full_name: str, today: str, n: int) -> list[int | None]:
        daily = self.get(full_name).get("daily", {})
        return [daily.get((_d(today) - timedelta(days=i)).isoformat()) for i in range(n - 1, -1, -1)]

    def followed(self, today: str, days: int, top: int) -> list[str]:
        out = []
        limit = (_d(today) - timedelta(days=days)).isoformat()
        for fn, e in self.repos.items():
            if any(d >= limit and d < today and rk <= top for d, rk in e.get("ranks", {}).items()):
                out.append(fn)
        return out

    # -- maintenance ---------------------------------------------------------------
    def purge(self, today: str, keep_days: int) -> int:
        limit = (_d(today) - timedelta(days=keep_days)).isoformat()
        removed = 0
        for fn in list(self.repos):
            e = self.repos[fn]
            for key in ("stars", "daily", "stars7d", "ranks"):
                if key in e:
                    e[key] = {d: v for d, v in e[key].items() if d >= limit}
            for key in ("top20", "sent"):
                if key in e:
                    e[key] = [d for d in e[key] if d >= limit]
            if not e.get("stars") and not e.get("ranks") and e.get("last_seen", "") < limit:
                del self.repos[fn]
                removed += 1
        return removed
