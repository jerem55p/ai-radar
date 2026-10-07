"""Filtres : licence OSI, lien avec l'IA, forks/archivés/miroirs, listes « awesome », étoiles suspectes."""
from __future__ import annotations

import re
from collections import Counter
from datetime import datetime, timedelta, timezone


def _compile(patterns: list[str]) -> list[re.Pattern]:
    return [re.compile(p, re.IGNORECASE) for p in patterns]


def ai_keyword_level(text: str, cfg: dict) -> str | None:
    """'strong' si un mot-clé IA fort est présent, 'weak' pour un mot faible, sinon None."""
    f = cfg["filters"]
    if any(p.search(text or "") for p in _compile(f["ai_keywords_strong"])):
        return "strong"
    if any(p.search(text or "") for p in _compile(f["ai_keywords_weak"])):
        return "weak"
    return None


def ai_relevance(repo: dict, cfg: dict) -> str | None:
    """'topic' | 'strong' | 'weak' (cas limite : README à vérifier) | None (hors sujet)."""
    topics = {t.lower() for t in repo.get("topics", [])}
    if topics & {t.lower() for t in cfg["filters"]["ai_topics"]}:
        return "topic"
    return ai_keyword_level(repo.get("description", ""), cfg)


def readme_confirms_ai(readme: str | None, cfg: dict) -> bool:
    if not readme:
        return False
    f = cfg["filters"]
    hits = sum(len(p.findall(readme)) for p in _compile(f["ai_keywords_strong"]))
    return hits >= f["readme_min_strong_hits"]


def is_excluded_list(repo: dict, cfg: dict) -> bool:
    f = cfg["filters"]
    if not f.get("exclude_lists", True):
        return False
    name = repo["full_name"]
    if any(p.search(name) for p in _compile(f["exclude_name_patterns"])):
        return True
    if any(p.search(repo.get("description", "")) for p in _compile(f["exclude_description_patterns"])):
        return True
    topics = {t.lower() for t in repo.get("topics", [])}
    return bool(topics & {t.lower() for t in f["exclude_topics"]})


def is_suspicious(repo: dict, cfg: dict) -> bool:
    s = cfg["filters"]["suspicious"]
    stars = repo.get("stars", 0)
    return stars > s["min_stars"] and (repo.get("forks", 0) / max(stars, 1)) < s["max_fork_ratio"]


def apply_filters(repos: dict[str, dict], cfg: dict, now: datetime) -> tuple[list[dict], Counter]:
    """Filtres peu coûteux (sans appel API). Les cas limites IA sont marqués `ai_borderline`."""
    f = cfg["filters"]
    allowed = set(f["licenses"])
    limit = now - timedelta(days=f["min_push_days"])
    stats: Counter = Counter()
    kept = []
    for repo in repos.values():
        if repo.get("fork"):
            stats["fork"] += 1
        elif repo.get("archived"):
            stats["archivé"] += 1
        elif repo.get("mirror"):
            stats["miroir"] += 1
        elif not repo.get("description"):
            stats["sans description"] += 1
        elif repo.get("license") not in allowed:
            stats["licence non OSI / absente"] += 1
        elif _pushed_before(repo, limit):
            stats["inactif > %d j" % f["min_push_days"]] += 1
        elif is_excluded_list(repo, cfg):
            stats["liste awesome / prompts / cours"] += 1
        else:
            rel = ai_relevance(repo, cfg)
            if rel is None:
                stats["hors IA"] += 1
                continue
            repo["ai_borderline"] = rel == "weak"
            repo["suspicious"] = is_suspicious(repo, cfg)
            kept.append(repo)
    return kept, stats


def _pushed_before(repo: dict, limit: datetime) -> bool:
    ts = repo.get("pushed_at")
    if not ts:
        return True
    try:
        pushed = datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return True
    return pushed < limit
