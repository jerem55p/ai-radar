"""Collecte des candidats : page Trending (fragile, isolée) + API Search + dépôts déjà suivis."""
from __future__ import annotations

import logging
import re
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

from bs4 import BeautifulSoup

from .filters import ai_keyword_level
from .http import GitHubClient, plain_get

log = logging.getLogger("ai_radar")
BROWSER_UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/124 Safari/537.36",
              "Accept-Language": "en-US,en;q=0.9"}


def to_int(text: str) -> int:
    digits = re.sub(r"[^\d]", "", text or "")
    return int(digits) if digits else 0


# ---------------------------------------------------------------- Trending ----
def parse_trending(html: str, period: str) -> list[dict]:
    """Extrait les dépôts d'une page Trending. `period` : daily | weekly."""
    soup = BeautifulSoup(html, "html.parser")
    out = []
    for art in soup.select("article.Box-row"):
        link = art.select_one("h2 a")
        if not link or not link.get("href"):
            continue
        full_name = link["href"].strip("/")
        if full_name.count("/") != 1:
            continue
        desc = art.select_one("p")
        lang = art.select_one("span[itemprop=programmingLanguage]")
        total = art.select_one("a[href$='/stargazers']")
        m = re.search(r"(\d[\d,\.]*)\s+stars?\s+(today|this week|this month)", art.get_text(" ", strip=True))
        out.append({
            "full_name": full_name,
            "description": desc.get_text(" ", strip=True) if desc else "",
            "language": lang.get_text(strip=True) if lang else None,
            "stars_total": to_int(total.get_text()) if total else 0,
            "period": period,
            "stars_period": to_int(m.group(1)) if m else 0,
        })
    return out


def collect_trending(cfg: dict) -> dict[str, dict]:
    """{full_name: {trend_day, trend_week, description, language}} — ne lève jamais d'exception."""
    tcfg = cfg["collect"]["trending"]
    result: dict[str, dict] = {}
    if not tcfg.get("enabled", True):
        return result
    pages = failures = 0
    try:
        for lang in tcfg["languages"]:
            for period in tcfg["periods"]:
                url = "https://github.com/trending" + (f"/{quote(lang)}" if lang else "")
                try:
                    r = plain_get(url, params={"since": period}, headers=BROWSER_UA, timeout=20, retries=2)
                    items = parse_trending(r.text, period) if r.status_code == 200 else []
                except Exception as exc:  # noqa: BLE001
                    log.warning("Trending %s/%s indisponible : %s", lang or "all", period, exc)
                    items, failures = [], failures + 1
                pages += 1
                for it in items:
                    e = result.setdefault(it["full_name"], {"description": it["description"],
                                                            "language": it["language"],
                                                            "stars_total": it["stars_total"]})
                    key = "trend_day" if period == "daily" else "trend_week"
                    e[key] = max(e.get(key, 0), it["stars_period"])
                time.sleep(tcfg.get("delay", 1.0))
    except Exception as exc:  # noqa: BLE001  (le scraping ne doit jamais bloquer le bot)
        log.warning("Scraping Trending interrompu : %s", exc)
    log.info("Trending : %d pages lues (%d échecs) → %d dépôts", pages, failures, len(result))
    return result


# ------------------------------------------------------------------ Search ----
def normalize_repo(item: dict, source: str) -> dict:
    lic = item.get("license") or {}
    return {
        "full_name": item["full_name"],
        "owner": (item.get("owner") or {}).get("login") or item["full_name"].split("/")[0],
        "html_url": item.get("html_url") or f"https://github.com/{item['full_name']}",
        "description": (item.get("description") or "").strip(),
        "topics": item.get("topics") or [],
        "language": item.get("language"),
        "license": lic.get("spdx_id"),
        "stars": item.get("stargazers_count", 0),
        "forks": item.get("forks_count", 0),
        "open_issues": item.get("open_issues_count", 0),
        "created_at": item.get("created_at"),
        "pushed_at": item.get("pushed_at"),
        "fork": bool(item.get("fork")),
        "archived": bool(item.get("archived")),
        "mirror": bool(item.get("mirror_url")),
        "sources": {source},
    }


def collect_search(gh: GitHubClient, cfg: dict, today: datetime) -> dict[str, dict]:
    s = cfg["collect"]["search"]
    created = (today - timedelta(days=s["created_days"])).date().isoformat()
    pushed = (today - timedelta(days=s["pushed_days"])).date().isoformat()
    repos: dict[str, dict] = {}
    queries = []
    for t in s["topics"]:
        queries.append((f"topic:{t} created:>{created} stars:>{s['created_min_stars']} archived:false",
                        "stars", "search"))
        queries.append((f"topic:{t} pushed:>{pushed} stars:>{s['pushed_min_stars']} archived:false",
                        "updated", "search"))
    for q, sort, src in queries:
        if gh.exhausted:
            log.warning("Search interrompue : quota épuisé.")
            break
        data = gh.json("/search/repositories", search=True,
                       params={"q": q, "sort": sort, "order": "desc", "per_page": s["per_page"]})
        for item in (data or {}).get("items", []):
            fn = item["full_name"]
            if fn in repos:
                repos[fn]["sources"].add(src)
            else:
                repos[fn] = normalize_repo(item, src)
    log.info("Search : %d requêtes → %d dépôts uniques", len(queries), len(repos))
    return repos


def fetch_repo(gh: GitHubClient, full_name: str, source: str) -> dict | None:
    data = gh.json(f"/repos/{full_name}")
    return normalize_repo(data, source) if data else None


def collect_all(gh: GitHubClient, cfg: dict, state, now: datetime, today: str) -> dict[str, dict]:
    """Retourne {full_name: repo} dédoublonné, avec les chiffres Trending fusionnés."""
    trending = collect_trending(cfg)
    repos = collect_search(gh, cfg, now)
    n_search = len(repos)

    # Trending : on complète via /repos/{o}/{r} les dépôts qui ressemblent à de l'IA
    cap = cfg["github"]["trending_detail_cap"]
    todo = [fn for fn, t in sorted(trending.items(), key=lambda kv: -kv[1].get("trend_day", 0))
            if fn not in repos and (ai_keyword_level(t["description"], cfg) or t.get("language") == "Jupyter Notebook")]
    n_trend = 0
    for fn in todo[:cap]:
        r = fetch_repo(gh, fn, "trending")
        if r:
            repos[fn] = r
            n_trend += 1
    for fn, t in trending.items():
        if fn in repos:
            repos[fn]["sources"].add("trending")
            repos[fn]["trend_day"] = t.get("trend_day")
            repos[fn]["trend_week"] = t.get("trend_week")

    # Dépôts déjà suivis (top 60 de ces 7 derniers jours)
    fcfg = cfg["collect"]["followed"]
    n_follow = 0
    for fn in state.followed(today, fcfg["days"], fcfg["top"]):
        if fn in repos:
            repos[fn]["sources"].add("followed")
        else:
            r = fetch_repo(gh, fn, "followed")
            if r:
                repos[fn] = r
                n_follow += 1
    log.info("Candidats : search=%d, trending (via API)=%d, suivis=%d → total %d",
             n_search, n_trend, n_follow, len(repos))
    return repos


def parse_ts(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None
