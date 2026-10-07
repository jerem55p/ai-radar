"""Rendu : message Telegram (HTML, découpé par catégorie) et pages web (Jinja2)."""
from __future__ import annotations

import html
import re
from datetime import date, datetime
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

TG_LIMIT = 4096
MAX_MESSAGES = 3
JOURS = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
MOIS = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre", "octobre",
        "novembre", "décembre"]


# ------------------------------------------------------------------ formats ----
def fr_date(d: date | str, weekday: bool = True) -> str:
    d = date.fromisoformat(d) if isinstance(d, str) else d
    s = f"{d.day}{'er' if d.day == 1 else ''} {MOIS[d.month - 1]} {d.year}"
    return f"{JOURS[d.weekday()]} {s}" if weekday else s


def fmt_int(n: int) -> str:
    return f"{int(n):,}".replace(",", " ")


def fmt_k(n: int) -> str:
    """12 345 → 12,3k ; 850 → 850."""
    if n < 1000:
        return str(n)
    if n < 100_000:
        return f"{n / 1000:.1f}k".replace(".", ",").replace(",0k", "k")
    return f"{n // 1000}k"


def age_label(days: float) -> str:
    if days < 1:
        return "< 1 jour"
    if days < 60:
        return f"{int(days)} j"
    if days < 700:
        return f"{int(days // 30)} mois"
    return f"{days / 365:.1f} ans".replace(".", ",")


def esc(s: str) -> str:
    return html.escape(s or "", quote=False)


def link(r: dict) -> str:
    return f'<a href="{html.escape(r["html_url"], quote=True)}">{esc(r["full_name"])}</a>'


def gain24(r: dict, n: int | None = None) -> str:
    """Étoiles gagnées en 24 h ; préfixé de ≈ quand le chiffre est extrapolé."""
    n = r["stars_24h"] if n is None else n
    return ("≈" if "extrapolé" in r.get("src_24h", "") else "") + "+" + fmt_int(n)


def badge_parts(r: dict) -> list[str]:
    """Badges texte dans l'ordre : 🆕, ↑↓, 🔁, 🔥HN."""
    out = []
    if r.get("is_new"):
        out.append("🆕")
    d = r.get("rank_delta", 0)
    if d > 0:
        out.append(f"↑{d}")
    elif d < 0:
        out.append(f"↓{-d}")
    if r.get("repeat"):
        out.append("🔁")
    if r.get("hn_badge"):
        out.append(f"🔥HN {r['hn_points']}")
    return out


# ----------------------------------------------------------------- Telegram ----
def first_sentences(text: str, n: int) -> str:
    parts = re.split(r"(?<=[.!?])\s+", text.strip())
    return " ".join(parts[:n])


def _podium_block(podium: list[dict], header: str, sentences: int) -> str:
    lines = [header, "", "🏆 <b>PODIUM</b>"]
    for i, r in enumerate(podium, start=1):
        badges = " ".join(badge_parts(r) + (["🧪"] if r.get("easy_try") else []))
        lines.append(f"{i}. {link(r)} — ⭐ {fmt_k(r['stars'])} ({gain24(r)} en 24 h)"
                     + (f" {badges}" if badges else ""))
        lines.append("   " + esc(first_sentences(r.get("details") or r.get("one_liner", ""), sentences)))
    return "\n".join(lines)


def _category_block(cat: dict, items: list[dict], trend: str | None, words: int | None) -> str:
    lines = [f"{cat['emoji']} <b>{esc(cat['name'].upper())}</b>"]
    if trend:
        lines.append(f"↳ {esc(trend)}")
    for r in items:
        one = r.get("one_liner", "")
        if words:
            one = " ".join(one.split()[:words])
        badges = " ".join(badge_parts(r))
        lines.append(f"#{r['rank']} {link(r)} {gain24(r)}⭐" + (f" {badges}" if badges else "")
                     + f" — {esc(one)}" + (" 🧪" if r.get("easy_try") else ""))
    return "\n".join(lines)


def pack_blocks(blocks: list[str], limit: int = TG_LIMIT) -> list[str]:
    """Regroupe des blocs en messages ≤ limit, sans jamais couper un bloc (sauf bloc géant : coupe par ligne)."""
    expanded: list[str] = []
    for b in blocks:
        if len(b) <= limit:
            expanded.append(b)
            continue
        cur = ""
        for line in b.split("\n"):
            if cur and len(cur) + len(line) + 1 > limit:
                expanded.append(cur)
                cur = line
            else:
                cur = f"{cur}\n{line}" if cur else line
        expanded.append(cur)
    messages, cur = [], ""
    for b in expanded:
        if cur and len(cur) + 2 + len(b) > limit:
            messages.append(cur)
            cur = b
        else:
            cur = f"{cur}\n\n{b}" if cur else b
    if cur:
        messages.append(cur)
    return messages


LEGEND = "🧪 facile à essayer · 🔥HN discuté sur Hacker News · 🆕 nouveau · ↑↓ rang vs hier · 🔁 ≥ 3 jours"


def build_telegram(top: list[dict], cfg: dict, day: str, page_url: str, trends: dict[str, str]) -> list[str]:
    """Construit 1 à 3 messages. `top` = projets à afficher (anti-monotonie déjà appliquée)."""
    cats = {c["id"]: c for c in cfg["categories"]}
    header = f"🤖 <b>AI Radar</b> — {fr_date(day)} · Top {cfg['top_n']} IA open source"
    footer = f"📊 Rapport complet et archives : {html.escape(page_url)}\n<i>{LEGEND}</i>"
    n_pod = cfg["podium"]

    def assemble(items: list[dict], with_trend: bool, sentences: int, words: int | None) -> list[str]:
        podium, rest = items[:n_pod], items[n_pod:]
        by_cat: dict[str, list[dict]] = {}
        for r in rest:
            by_cat.setdefault(r["category"], []).append(r)
        order = sorted(by_cat, key=lambda c: min(x["rank"] for x in by_cat[c]))
        blocks = [_podium_block(podium, header, sentences)]
        blocks += [_category_block(cats[c], by_cat[c], trends.get(c) if with_trend else None, words) for c in order]
        blocks.append(footer)
        return pack_blocks(blocks)

    items = list(top)
    attempts = [(True, 2, None), (False, 2, None), (False, 1, 12), (False, 1, 8)]
    msgs: list[str] = []
    for with_trend, sent, words in attempts:
        msgs = assemble(items, with_trend, sent, words)
        if len(msgs) <= MAX_MESSAGES:
            return msgs
    while len(msgs) > MAX_MESSAGES and len(items) > n_pod:  # dernier recours : on retire les derniers rangs
        items.pop()
        msgs = assemble(items, False, 1, 8)
    return msgs


def build_weekly_telegram(week: dict, cfg: dict, day: str, page_url: str) -> list[str]:
    cats = {c["id"]: c for c in cfg["categories"]}
    lines = [f"📅 <b>AI Radar — récap de la semaine</b> ({fr_date(week['start'], False)} → {fr_date(day, False)})", "",
             "🏆 <b>TOP 10 DE LA SEMAINE</b> (étoiles gagnées sur 7 jours)"]
    for i, r in enumerate(week["top10"], start=1):
        lines.append(f"{i}. {link(r)} +{fmt_int(r['gain_7d'])}⭐ — {esc(r.get('one_liner', ''))}")
    if not week["enough_history"]:
        lines += ["", "ℹ️ Historique encore court : les listes « se confirment » et « feux de paille » apparaîtront après quelques jours."]
    else:
        if week["confirmed"]:
            lines += ["", "✅ <b>Se confirment</b> (≥ 4 jours sur 7 dans le top 20)"]
            lines += [f"• {link(r)} ({r['days_in_top']} j)" for r in week["confirmed"][:8]]
        if week["flashes"]:
            lines += ["", "💨 <b>Feux de paille</b> (un seul gros pic)"]
            lines += [f"• {link(r)}" for r in week["flashes"][:8]]
    c = week.get("top_category")
    if c and c["id"] in cats:
        lines += ["", f"🔥 <b>Catégorie la plus active</b> : {cats[c['id']]['emoji']} {esc(cats[c['id']]['name'])} "
                      f"(+{fmt_int(c['gain'])}⭐ cumulées)"]
    lines += ["", f"📊 Page de la semaine : {html.escape(page_url)}"]
    return pack_blocks(["\n".join(lines)])


# ---------------------------------------------------------------- pages web ----
def sparkline(values: list[int | None], w: int = 140, h: int = 36) -> str:
    """Mini-graphique en barres (SVG inline) des étoiles gagnées par jour sur 14 jours."""
    vals = [v for v in values if v is not None]
    if len(vals) < 2:
        return '<span class="muted small">historique en construction</span>'
    mx = max(vals) or 1
    n = len(values)
    gap = 2
    bw = (w - gap * (n - 1)) / n
    bars = []
    for i, v in enumerate(values):
        x = i * (bw + gap)
        if v is None:
            bars.append(f'<rect class="bar-none" x="{x:.1f}" y="{h - 1}" width="{bw:.1f}" height="1"/>')
            continue
        bh = max(1.5, (v / mx) * (h - 4))
        cls = "bar bar-last" if i == n - 1 else "bar"
        bars.append(f'<rect class="{cls}" x="{x:.1f}" y="{h - bh:.1f}" width="{bw:.1f}" height="{bh:.1f}" rx="1"/>')
    title = f"Étoiles gagnées par jour sur {n} jours (max {fmt_int(mx)})"
    return (f'<svg class="spark" viewBox="0 0 {w} {h}" width="{w}" height="{h}" role="img" '
            f'aria-label="{html.escape(title)}"><title>{html.escape(title)}</title>{"".join(bars)}</svg>')


def to_view(r: dict, cfg: dict, series: list[int | None] | None, now: datetime) -> dict:
    cat = next((c for c in cfg["categories"] if c["id"] == r["category"]), cfg["categories"][0])
    badges = []
    if r.get("is_new"):
        badges.append(("🆕 nouveau", "new"))
    d = r.get("rank_delta", 0)
    if d:
        badges.append((f"{'↑' if d > 0 else '↓'}{abs(d)} vs hier", "up" if d > 0 else "down"))
    if r.get("repeat"):
        badges.append((f"🔁 {r.get('streak')} jours", "repeat"))
    if r.get("hn_badge"):
        badges.append((f"🔥 HN {r['hn_points']}", "hn"))
    if r.get("easy_try"):
        badges.append(("🧪 facile à essayer", "try"))
    if r.get("stagnant"):
        badges.append(("💤 en perte de vitesse", "stale"))
    if r.get("suspicious"):
        badges.append(("⚠️ étoiles suspectes", "warn"))
    pushed = (r.get("pushed_at") or "")[:10]
    if r.get("details", "").startswith((r.get("one_liner") or "@@@").rstrip("…")):
        r = {**r, "details": ""}  # texte de secours : la phrase courte est déjà le début de la description
    return {**r, "cat_id": cat["id"], "cat_emoji": cat["emoji"], "cat_name": cat["name"], "badges": badges,
            "stars_fmt": fmt_int(r["stars"]), "s24": gain24(r), "s7": ("≥" if not r.get("stars_7d_known", True) else "") + "+" + fmt_int(r["stars_7d"]),
            "spark": sparkline(series or []), "age": age_label(r.get("age_days", 0)),
            "pushed": fr_date(pushed, False) if pushed else "—", "license_label": r.get("license") or "—"}


def make_env(templates_dir: Path) -> Environment:
    return Environment(loader=FileSystemLoader(str(templates_dir)), autoescape=select_autoescape(["html", "j2"]),
                       trim_blocks=True, lstrip_blocks=True)


def render_day(env: Environment, cfg: dict, day: str, top: list[dict], watch: list[dict], series: dict,
               now: datetime, trends: dict, archives: list[dict] | None, page_url: str) -> str:
    views = [to_view(r, cfg, series.get(r["full_name"]), now) for r in top]
    cat_ids = []
    for v in views:
        if v["cat_id"] not in cat_ids:
            cat_ids.append(v["cat_id"])
    cats = [c for c in cfg["categories"] if c["id"] in cat_ids]
    return env.get_template("day.html.j2").render(
        day=day, day_label=fr_date(day), items=views, podium=views[:cfg["podium"]], rest=views[cfg["podium"]:],
        watch=[{**w, "cat_emoji": next((c["emoji"] for c in cfg["categories"] if c["id"] == w["category"]), ""),
                "s24": fmt_int(w["stars_24h"])} for w in watch],
        cats=cats, trends=trends, archives=archives, page_url=page_url, top_n=cfg["top_n"],
        generated=now.strftime("%Y-%m-%d %H:%M UTC"), is_index=archives is not None)


def render_weekly(env: Environment, cfg: dict, week: dict, day: str, now: datetime, page_url: str) -> str:
    cats = {c["id"]: c for c in cfg["categories"]}

    def view(r: dict) -> dict:
        c = cats.get(r.get("category"), cfg["categories"][0])
        return {**r, "cat_emoji": c["emoji"], "cat_name": c["name"], "gain": fmt_int(r.get("gain_7d", 0))}

    tc = week.get("top_category")
    return env.get_template("weekly.html.j2").render(
        day=day, start_label=fr_date(week["start"], False), end_label=fr_date(day, False),
        top10=[view(r) for r in week["top10"]], confirmed=[view(r) for r in week["confirmed"]],
        flashes=[view(r) for r in week["flashes"]], enough=week["enough_history"],
        top_category=(f"{cats[tc['id']]['emoji']} {cats[tc['id']]['name']}" if tc and tc["id"] in cats else None),
        top_gain=fmt_int(tc["gain"]) if tc else None, page_url=page_url, generated=now.strftime("%Y-%m-%d %H:%M UTC"))


def render_email(env: Environment, cfg: dict, day: str, top: list[dict], watch: list[dict], trends: dict,
                 now: datetime, page_url: str) -> str:
    """Version e-mail du rapport du jour : styles en ligne, sans JavaScript ni variables CSS."""
    views = []
    for r in top:
        v = to_view(r, cfg, None, now)
        v["badge_texts"] = badge_parts(r) + (["🧪"] if r.get("easy_try") else [])
        views.append(v)
    n_pod = cfg["podium"]
    by_cat: dict[str, list[dict]] = {}
    for v in views[n_pod:]:
        by_cat.setdefault(v["cat_id"], []).append(v)
    order = sorted(by_cat, key=lambda c: min(x["rank"] for x in by_cat[c]))
    cats = [c for cid in order for c in cfg["categories"] if c["id"] == cid]
    wviews = [{**w, "s24": fmt_int(w["stars_24h"])} for w in watch]
    return env.get_template("email.html.j2").render(
        kind="daily", title=f"AI Radar — {fr_date(day)}", subtitle=f"Top {cfg['top_n']} IA open source · {fr_date(day)}",
        podium=views[:n_pod], cats=cats, by_cat=by_cat, trends=trends, watch=wviews, page_url=page_url,
        generated=now.strftime("%Y-%m-%d %H:%M UTC"))


def render_email_weekly(env: Environment, cfg: dict, week: dict, day: str, now: datetime, page_url: str) -> str:
    cats = {c["id"]: c for c in cfg["categories"]}

    def view(r: dict) -> dict:
        c = cats.get(r.get("category"), cfg["categories"][0])
        return {**r, "cat_emoji": c["emoji"], "cat_name": c["name"], "gain": fmt_int(r.get("gain_7d", 0))}

    tc = week.get("top_category")
    return env.get_template("email.html.j2").render(
        kind="weekly", title="AI Radar — récap de la semaine",
        subtitle=f"Récap de la semaine · {fr_date(week['start'], False)} → {fr_date(day, False)}",
        top10=[view(r) for r in week["top10"]], confirmed=[view(r) for r in week["confirmed"]],
        flashes=[view(r) for r in week["flashes"]], enough=week["enough_history"],
        top_category=(f"{cats[tc['id']]['emoji']} {cats[tc['id']]['name']}" if tc and tc["id"] in cats else None),
        top_gain=fmt_int(tc["gain"]) if tc else None, page_url=page_url, generated=now.strftime("%Y-%m-%d %H:%M UTC"))


def archive_list(docs: Path) -> list[dict]:
    """Liste des pages existantes dans docs/ (jours et semaines), du plus récent au plus ancien."""
    out = []
    for p in docs.glob("*.html"):
        m = re.fullmatch(r"(\d{4}-\d{2}-\d{2})\.html", p.name)
        w = re.fullmatch(r"semaine-(\d{4}-\d{2}-\d{2})\.html", p.name)
        if m:
            out.append({"file": p.name, "date": m.group(1), "label": fr_date(m.group(1)), "kind": "day"})
        elif w:
            out.append({"file": p.name, "date": w.group(1), "label": "Récap de la semaine — " + fr_date(w.group(1), False),
                        "kind": "week"})
    return sorted(out, key=lambda a: (a["date"], a["kind"] == "week"), reverse=True)
