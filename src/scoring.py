"""Pré-classement, score global, répartition par catégorie (quotas), badges, anti-monotonie."""
from __future__ import annotations

import math
import re
from collections import Counter
from datetime import datetime


# ------------------------------------------------------------ pré-classement ----
def fast_score(repo: dict, state, cfg: dict, now: datetime, today: str, age: float) -> float:
    """Score rapide avec des données gratuites (Trending, âge, push, historique)."""
    hist24 = state.stars_diff(repo["full_name"], today, 1, repo["stars"])
    est24 = max(repo.get("trend_day") or 0, hist24 or 0,
                (repo["stars"] / max(age, 1.0)) if age <= 45 else 0,
                (repo.get("trend_week") or 0) / 7)
    repo["est24"] = est24
    rel = est24 / max(repo["stars"] - est24 + 50, 1)
    fresh = max(0.0, 1 - age / cfg["score"]["fresh_days"])
    boost = 0.5 if "followed" in repo.get("sources", ()) else 0.0
    return 0.5 * math.log1p(est24) / 8 + 0.25 * min(rel, 2) / 2 + 0.15 * fresh + 0.1 * (1 if est24 else 0) + boost * 0.1


def prerank(repos: list[dict], state, cfg: dict, now: datetime, today: str, ages: dict[str, float]) -> list[dict]:
    for r in repos:
        r["fast_score"] = fast_score(r, state, cfg, now, today, ages[r["full_name"]])
    return sorted(repos, key=lambda r: -r["fast_score"])[: cfg["prerank"]["keep"]]


# ------------------------------------------------------------------- score ----
def _percentile(sorted_vals: list[float], p: float) -> float:
    if not sorted_vals:
        return 0.0
    k = (len(sorted_vals) - 1) * p
    lo, hi = math.floor(k), math.ceil(k)
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (k - lo)


def normalize(values: list[float], clip_pct: float | None = None) -> list[float]:
    """Normalisation min-max sur [0, 1], avec écrêtage optionnel au percentile `clip_pct`."""
    if not values:
        return []
    vals = list(values)
    if clip_pct is not None:
        cap = _percentile(sorted(vals), clip_pct)
        vals = [min(v, cap) for v in vals]
    lo, hi = min(vals), max(vals)
    if hi - lo < 1e-12:
        return [0.0] * len(vals)
    return [(v - lo) / (hi - lo) for v in vals]


def score_all(repos: list[dict], cfg: dict, ages: dict[str, float]) -> list[dict]:
    """Calcule `score` et `components` pour chaque dépôt, retourne la liste triée par score décroissant."""
    if not repos:
        return []
    sc = cfg["score"]
    w = sc["weights"]
    s24 = [r["stars_24h"] for r in repos]
    s7 = [r["stars_7d"] for r in repos]
    vel_abs = normalize([math.log1p(x) for x in s24])
    vel_rel = normalize([r["stars_24h"] / (r["stars"] - r["stars_24h"] + 50) for r in repos], sc["clip_percentile"])
    # accélération : inconnue (7 j non mesurables) → valeur neutre = médiane des dépôts mesurés
    raw_acc = [a / (b / 7 + 1) if r.get("stars_7d_known", True) else None for r, a, b in zip(repos, s24, s7)]
    known = sorted(v for v in raw_acc if v is not None)
    neutral = known[len(known) // 2] if known else 0.0
    acc = normalize([neutral if v is None else v for v in raw_acc], sc["clip_percentile"])
    act = normalize([math.log1p(r.get("commits_7d", 0)) for r in repos])
    buzz = normalize([math.log1p(r.get("hn_points", 0)) for r in repos])
    for i, r in enumerate(repos):
        fresh = max(0.0, 1 - ages[r["full_name"]] / sc["fresh_days"])
        activity = 0.7 * act[i] + 0.3 * (1.0 if r.get("release_recent") else 0.0)
        comp = {"velocity_abs": vel_abs[i], "velocity_rel": vel_rel[i], "acceleration": acc[i],
                "freshness": fresh, "activity": activity, "buzz": buzz[i]}
        score = sum(w[k] * v for k, v in comp.items())
        if r.get("suspicious"):
            score *= cfg["filters"]["suspicious"]["penalty"]
        r["components"], r["score"] = comp, score
    return sorted(repos, key=lambda r: -r["score"])


# --------------------------------------------------------- catégorisation (règles) ----
def rule_category(repo: dict, cfg: dict) -> str:
    """Catégorie de secours déduite des topics (poids 3) et de la description (poids 1)."""
    topics = {t.lower() for t in repo.get("topics", [])}
    text = repo.get("description", "")
    best, best_score = cfg["default_category"], 0
    for cat in cfg["categories"]:
        s = 3 * len(topics & {t.lower() for t in cat["topics"]})
        s += sum(1 for p in cat["keywords"] if re.search(p, text, re.IGNORECASE))
        if s > best_score:
            best, best_score = cat["id"], s
    return best


# ------------------------------------------------------------- répartition ----
def distribute(ranked: list[dict], cfg: dict) -> list[dict]:
    """Sélectionne le top N avec quotas par catégorie et par propriétaire, puis applique la règle de secours.

    `ranked` : dépôts triés par score, chacun avec une clé `category`.
    Retourne la liste finale triée par score, avec `rank` (1..N).
    """
    n, max_cat, max_owner = cfg["top_n"], cfg["max_per_category"], cfg["max_per_owner"]
    selected: list[dict] = []
    cats: Counter = Counter()
    owners: Counter = Counter()

    def can_take(r: dict) -> bool:
        return cats[r["category"]] < max_cat and owners[r["owner"]] < max_owner

    def take(r: dict) -> None:
        selected.append(r)
        cats[r["category"]] += 1
        owners[r["owner"]] += 1

    for r in ranked:
        if len(selected) >= n:
            break
        if can_take(r):
            take(r)

    # Règle de secours : une catégorie sans projet récupère la place du dernier si son meilleur
    # candidat a un score >= rescue_ratio × score du 20e.
    if len(selected) >= n:
        threshold = cfg["rescue_ratio"] * selected[-1]["score"]
        rescued: list[dict] = []
        for cat in cfg["categories"]:
            if cats[cat["id"]] > 0:
                continue
            chosen = {r["full_name"] for r in selected}
            cand = next((r for r in ranked if r["category"] == cat["id"] and r["full_name"] not in chosen
                         and r["score"] >= threshold and owners[r["owner"]] < max_owner), None)
            if cand is None:
                continue
            victim = next((v for v in reversed(selected)
                           if v not in rescued and cats[v["category"]] > 1), None)
            if victim is None:
                continue
            selected.remove(victim)
            cats[victim["category"]] -= 1
            owners[victim["owner"]] -= 1
            take(cand)
            rescued.append(cand)
            cand["rescued"] = True

    selected.sort(key=lambda r: -r["score"])
    for i, r in enumerate(selected, start=1):
        r["rank"] = i
    return selected


def extended_list(top: list[dict], ranked: list[dict]) -> list[dict]:
    """Top final puis le reste du classement : sert aux rangs historisés et à « À surveiller »."""
    chosen = {r["full_name"] for r in top}
    rest = [r for r in ranked if r["full_name"] not in chosen]
    for i, r in enumerate(rest, start=len(top) + 1):
        r["rank"] = i
    return top + rest


# ------------------------------------------------------------------ badges ----
def apply_badges(top: list[dict], state, cfg: dict, today: str) -> None:
    """🆕 nouveau, ↑n/↓n vs veille, 🔁 présent depuis >= 3 jours, + anti-monotonie (`stagnant`)."""
    prev_day = state.previous_date(today)
    rp = cfg["repeat"]
    for r in top:
        fn = r["full_name"]
        r["is_new"] = not state.top20_before(fn, today)
        prev_rank = state.rank_on(fn, prev_day)
        r["rank_delta"] = (prev_rank - r["rank"]) if (prev_rank is not None and not r["is_new"]) else 0
        r["streak"] = state.streak(fn, today)
        r["repeat"] = r["streak"] >= rp["streak_badge_days"]
        r["stagnant"] = is_stagnant(state, fn, today, r["streak"], r["stars_24h"],
                                    rp["stale_after_days"], rp["decline_days"])


def is_stagnant(state, full_name: str, today: str, streak: int, stars_24h: int,
                stale_after: int, decline_days: int) -> bool:
    """Dans le top depuis plus de `stale_after` jours et stars_24h en baisse `decline_days` jours de suite."""
    if streak <= stale_after:
        return False
    series = state.daily_series(full_name, today, decline_days + 1)  # du plus ancien à aujourd'hui
    series[-1] = stars_24h
    if any(v is None for v in series):
        return False
    return all(series[i] > series[i + 1] for i in range(len(series) - 1))
