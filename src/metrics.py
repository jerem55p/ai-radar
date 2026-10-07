"""Mesures fines : étoiles 24 h / 7 j, commits, release, Hacker News, README."""
from __future__ import annotations

import logging
import re
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

from .collect import parse_ts
from .http import GitHubClient, plain_get

log = logging.getLogger("ai_radar")


# ------------------------------------------------------------------ étoiles ----
# NB : l'endpoint /stargazers (avec `starred_at`) renvoie 404 pour les dépôts tiers avec les jetons
# actuels (constaté en octobre 2026, y compris via GraphQL). On reconstitue donc les étoiles récentes
# à partir des WatchEvent du flux d'événements du dépôt (/repos/{o}/{r}/events), puis de l'historique.
def count_watch_events(pages: list[list[dict]], now: datetime, complete: bool) -> dict:
    """Compte les étoiles (WatchEvent) des 24 h / 7 j à partir des pages d'événements d'un dépôt.

    `complete` : la dernière page n'est pas pleine, on a donc tout l'historique d'événements du dépôt.
    Le flux n'est pas strictement trié dans le temps (quelques événements anciens s'intercalent) : le début
    de la fenêtre fiable est le 5ᵉ centile des dates de la dernière page, pas le minimum.
    `covers24` / `covers7` : la fenêtre remonte assez loin pour que le compte soit exact.
    """
    all_ts = [(parse_ts(ev.get("created_at")), ev.get("type") == "WatchEvent") for page in pages for ev in page]
    all_ts = [(t, w) for t, w in all_ts if t is not None]
    last_page = sorted(t for t in (parse_ts(ev.get("created_at")) for ev in (pages[-1] if pages else [])) if t)
    if not all_ts or not last_page:
        return {"count24": 0, "count7": 0, "covers24": complete, "covers7": complete, "extrap24": None, "daily": {}}
    start = last_page[int(len(last_page) * 0.05)]
    in_window = [t for t, w in all_ts if w and (complete or t >= start)]
    span_h = max((now - start).total_seconds() / 3600, 0.0)
    c24 = sum(1 for t in in_window if t >= now - timedelta(hours=24))
    c7 = sum(1 for t in in_window if t >= now - timedelta(days=7))
    covers24 = complete or start <= now - timedelta(hours=24)
    covers7 = complete or start <= now - timedelta(days=7)
    extrap = None
    if not covers24 and span_h >= 0.5 and len(in_window) >= 10:
        extrap = len(in_window) / span_h * 24  # débit observé extrapolé sur 24 h
    daily: dict[str, int] = {}
    for t in in_window:  # seuls les jours entièrement couverts sont fiables
        if complete or t.date() > start.date():
            daily[t.date().isoformat()] = daily.get(t.date().isoformat(), 0) + 1
    return {"count24": c24, "count7": c7, "covers24": covers24, "covers7": covers7, "extrap24": extrap,
            "daily": daily}


def fetch_star_activity(gh: GitHubClient, full_name: str, now: datetime, max_pages: int = 3) -> dict | None:
    pages: list[list[dict]] = []
    complete = False
    for page in range(1, max_pages + 1):
        r = gh.get(f"/repos/{full_name}/events", params={"per_page": 100, "page": page})
        if r is None or r.status_code != 200:  # 422 : pagination plafonnée à 300 événements
            break
        items = r.json()
        if not items:
            complete = bool(pages)
            break
        pages.append(items)
        if len(items) < 100:
            complete = True
            break
        stamps = sorted(t for t in (parse_ts(i.get("created_at")) for i in items) if t)
        if stamps and stamps[int(len(stamps) * 0.05)] <= now - timedelta(days=7):
            break
    if not pages:
        return None
    return count_watch_events(pages, now, complete)


def resolve_stars(repo: dict, activity: dict | None, hist24: int | None, hist7: int | None, age: float) -> None:
    """Fixe stars_24h, stars_7d (et `stars_7d_known`) avec la meilleure source disponible.

    24 h : événements exacts > historique (snapshot de la veille) > max(Trending, extrapolation, âge ≤ 1 j).
    7 j  : événements exacts > historique > Trending hebdo > âge ≤ 7 j (= toutes les étoiles) > inconnu.
    """
    total = repo["stars"]
    act = activity or {}
    if act.get("covers24"):
        v24, src = act["count24"], "événements"
    elif hist24 is not None:
        v24, src = hist24, "historique"
    else:
        cands = []
        if repo.get("trend_day"):
            cands.append((repo["trend_day"], "Trending"))
        if act.get("extrap24") is not None:
            cands.append((int(round(act["extrap24"])), "événements (extrapolé)"))
        if age <= 1:
            cands.append((total, "âge < 1 j"))
        v24, src = max(cands, default=(0, "inconnu"))
    v24 = min(v24, total)
    known = True
    if act.get("covers7"):
        v7, src7 = act["count7"], "événements"
    elif hist7 is not None:
        v7, src7 = hist7, "historique"
    elif repo.get("trend_week"):
        v7, src7 = repo["trend_week"], "Trending"
    elif age <= 7:
        v7, src7 = total, "âge < 7 j"
    else:
        v7, src7, known = max(act.get("count7", 0), v24), "inconnu", False
    repo.update(stars_24h=v24, src_24h=src, stars_7d=max(min(v7, total), v24), src_7d=src7, stars_7d_known=known,
                daily_backfill=act.get("daily", {}))


# ------------------------------------------------------------ commits / release ----
def fetch_commits_7d(gh: GitHubClient, full_name: str, now: datetime) -> int | None:
    """/stats/commit_activity (réponse 202 → un retry), sinon repli sur /commits?since=."""
    for attempt in range(2):
        r = gh.get(f"/repos/{full_name}/stats/commit_activity")
        if r is None:
            break
        if r.status_code == 200:
            try:
                weeks = r.json()
                return int(weeks[-1]["total"]) if weeks else 0
            except (ValueError, KeyError, IndexError, TypeError):
                break
        if r.status_code == 202 and attempt == 0:
            time.sleep(2.5)
            continue
        break
    since = (now - timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%SZ")
    data = gh.json(f"/repos/{full_name}/commits", params={"since": since, "per_page": 100})
    return len(data) if isinstance(data, list) else None


def fetch_release(gh: GitHubClient, full_name: str, now: datetime, recent_days: int) -> dict:
    r = gh.get(f"/repos/{full_name}/releases/latest")
    if r is None or r.status_code != 200:
        return {"release_recent": False, "release_tag": None}
    j = r.json()
    ts = parse_ts(j.get("published_at"))
    return {"release_recent": bool(ts and ts >= now - timedelta(days=recent_days)),
            "release_tag": j.get("tag_name")}


def fetch_readme(gh: GitHubClient, full_name: str, limit: int = 8000) -> str | None:
    r = gh.get(f"/repos/{full_name}/readme", headers={"Accept": "application/vnd.github.raw+json"})
    if r is None or r.status_code != 200:
        return None
    return r.text[:limit]


# ------------------------------------------------------------- Hacker News ----
def fetch_hn(full_name: str, now: datetime, days: int = 7) -> dict:
    """Signal HN (API Algolia, sans clé). Ne lève jamais d'exception."""
    try:
        r = plain_get("https://hn.algolia.com/api/v1/search",
                      params={"query": f"github.com/{full_name}", "restrictSearchableAttributes": "url",
                              "tags": "story"}, timeout=10, retries=2)
        if r.status_code != 200:
            return {}
        best = None
        pat = re.compile(rf"github\.com/{re.escape(full_name)}(?:[/?#.].*)?$", re.IGNORECASE)
        for hit in r.json().get("hits", []):
            if not pat.search(hit.get("url") or ""):
                continue
            created = datetime.fromtimestamp(hit.get("created_at_i", 0), tz=timezone.utc)
            if created < now - timedelta(days=days):
                continue
            if best is None or (hit.get("points") or 0) > best["hn_points"]:
                best = {"hn_points": hit.get("points") or 0,
                        "hn_url": f"https://news.ycombinator.com/item?id={hit['objectID']}"}
        return best or {}
    except Exception as exc:  # noqa: BLE001
        log.debug("HN indisponible pour %s : %s", full_name, exc)
        return {}


# ----------------------------------------------------------------- badges ----
def easy_to_try(readme: str | None, patterns: list[str]) -> bool:
    if not readme:
        return False
    return any(re.search(p, readme, re.IGNORECASE) for p in patterns)


def age_days(repo: dict, now: datetime) -> float:
    created = parse_ts(repo.get("created_at"))
    return max((now - created).total_seconds() / 86400, 0.0) if created else 9999.0


# ---------------------------------------------------------- orchestration ----
def measure_all(gh: GitHubClient, repos: list[dict], state, cfg: dict, now: datetime, today: str) -> Counter:
    """Mesures coûteuses pour les dépôts pré-sélectionnés (en parallèle, quota respecté)."""
    stats: Counter = Counter()
    max_pages = cfg["github"]["events_max_pages"]
    recent = cfg["score"]["release_recent_days"]
    hn_days = cfg["score"]["hn_days"]
    hn_min = cfg["score"]["hn_badge_min_points"]

    def work(repo: dict) -> None:
        fn = repo["full_name"]
        hist24 = state.stars_diff(fn, today, 1, repo["stars"])
        hist7 = state.stars_diff(fn, today, 7, repo["stars"])
        activity = None
        try:
            activity = fetch_star_activity(gh, fn, now, max_pages)
        except Exception as exc:  # noqa: BLE001
            log.debug("événements %s : %s", fn, exc)
        resolve_stars(repo, activity, hist24, hist7, repo.get("age_days", 9999.0))
        stats["étoiles via " + repo["src_24h"]] += 1
        commits = fetch_commits_7d(gh, fn, now)
        repo["commits_7d"] = commits if commits is not None else 0
        repo.update(fetch_release(gh, fn, now, recent))
        repo.update({"hn_points": 0, "hn_url": None, **fetch_hn(fn, now, hn_days)})
        repo["hn_badge"] = repo["hn_points"] >= hn_min

    with ThreadPoolExecutor(max_workers=cfg["github"]["workers"]) as pool:
        list(pool.map(work, repos))
    return stats
