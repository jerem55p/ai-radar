"""AI Radar — orchestration et ligne de commande.

    python -m src.main --dry-run          # affiche le message, génère la page dans out/, n'envoie ni ne committe rien
    python -m src.main --dry-run --top 10
    python -m src.main --weekly           # force le récap hebdomadaire
"""
from __future__ import annotations

import argparse
import html
import logging
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

from . import collect, enrich, filters, metrics, notify, render, scoring, weekly
from .http import GitHubClient
from .state import State

ROOT = Path(__file__).resolve().parent.parent
log = logging.getLogger("ai_radar")
SECRET_ENV = ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "ANTHROPIC_API_KEY", "GH_PAT", "GITHUB_TOKEN",
              "SMTP_PASSWORD")


class SecretScrubber(logging.Filter):
    """Masque toute valeur de secret qui apparaîtrait dans un message de log."""

    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        for name in SECRET_ENV:
            val = os.environ.get(name)
            if val and len(val) > 6 and val in msg:
                msg = msg.replace(val, "***")
        record.msg, record.args = msg, ()
        return True


def setup_logging(verbose: bool) -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            pass
    handler = logging.StreamHandler()
    handler.addFilter(SecretScrubber())
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(message)s", "%H:%M:%S"))
    logging.basicConfig(level=logging.DEBUG if verbose else logging.INFO, handlers=[handler], force=True)
    for noisy in ("httpx", "httpcore", "anthropic", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def load_config(path: Path | None = None) -> dict:
    return yaml.safe_load((path or ROOT / "config.yaml").read_text(encoding="utf-8"))


def page_url(cfg: dict) -> str:
    u = cfg["user"]
    return u.get("pages_url") or f"https://{u['github_user']}.github.io/{u['repo_name']}/"


def set_output(key: str, value: str) -> None:
    path = os.environ.get("GITHUB_OUTPUT")
    if path:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(f"{key}={value}\n")


def plain_text(msg: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", msg))


# --------------------------------------------------------------------------------------
def run(args: argparse.Namespace, cfg: dict, state: State, now: datetime, today: str, is_sunday: bool) -> int:
    t0 = time.time()
    if args.top:
        cfg["top_n"] = args.top
    token = os.environ.get("GH_PAT") or os.environ.get("GITHUB_TOKEN")
    gh = GitHubClient(token, cfg["github"]["max_requests"], cfg["github"]["search_interval"])
    if not token:
        log.warning("Aucun GH_PAT / GITHUB_TOKEN : quota GitHub très réduit (60 requêtes/h).")
    gh.init_budget()

    # 1. collecte ------------------------------------------------------------------
    repos = collect.collect_all(gh, cfg, state, now, today)
    # 2. filtres --------------------------------------------------------------------
    cands, stats = filters.apply_filters(repos, cfg, now)
    log.info("Filtres : %d candidats → %d gardés. Éliminés : %s", len(repos), len(cands),
             ", ".join(f"{k}={v}" for k, v in stats.most_common()))
    for r in cands:
        r["age_days"] = metrics.age_days(r, now)
    ages = {r["full_name"]: r["age_days"] for r in cands}
    # 3. pré-classement + mesures --------------------------------------------------------
    short = scoring.prerank(cands, state, cfg, now, today, ages)
    log.info("Pré-classement : %d dépôts retenus pour les mesures fines", len(short))
    mstats = metrics.measure_all(gh, short, state, cfg, now, today)
    log.info("Sources des étoiles : %s", ", ".join(f"{k}={v}" for k, v in mstats.most_common()))
    floor = cfg["filters"]["min_stars_24h"]
    weak = [r for r in short if r["stars_24h"] < floor]
    if weak:
        log.info("Signal trop faible (< %d étoiles / 24 h) : %d dépôts écartés", floor, len(weak))
    short = [r for r in short if r["stars_24h"] >= floor]
    ranked = scoring.score_all(short, cfg, ages)

    # README du « pool » : cas limites IA, badge 🧪, résumés
    pool = ranked[: cfg["prerank"]["enrich_pool"]]
    readmes: dict[str, str] = {}

    def get_readme(r: dict) -> None:
        txt = metrics.fetch_readme(gh, r["full_name"])
        if txt:
            readmes[r["full_name"]] = txt

    with ThreadPoolExecutor(max_workers=cfg["github"]["workers"]) as ex:
        list(ex.map(get_readme, pool))
    dropped = []
    for r in list(short):
        if r.get("ai_borderline") and not filters.readme_confirms_ai(readmes.get(r["full_name"]), cfg):
            dropped.append(r["full_name"])
            short.remove(r)
    if dropped:
        log.info("Cas limites IA écartés après lecture du README : %s", ", ".join(dropped))
    for r in short:
        r["easy_try"] = metrics.easy_to_try(readmes.get(r["full_name"]), cfg["easy_try_patterns"])
    ranked = scoring.score_all(short, cfg, ages)

    # 4. catégories + résumés ------------------------------------------------------------
    enr = enrich.Enricher(cfg)
    for r in ranked:
        e = state.get(r["full_name"])
        if e.get("category") in {c["id"] for c in cfg["categories"]}:
            r["category"] = e["category"]
        if e.get("one_liner") and e.get("details") and not (e.get("fallback") and enr.client):
            r["one_liner"], r["details"] = e["one_liner"], e["details"]
            r["fallback"] = bool(e.get("fallback"))
    pool_names = {r["full_name"] for r in ranked[: cfg["prerank"]["enrich_pool"]]}
    enr.summarize([r for r in ranked if r["full_name"] in pool_names], readmes)
    for r in ranked:
        r.setdefault("category", scoring.rule_category(r, cfg))

    # 5. top N avec quotas, badges --------------------------------------------------------------
    top = scoring.distribute(ranked, cfg)
    extended = scoring.extended_list(top, ranked)
    watch = extended[len(top): len(top) + cfg["watch_count"]]
    missing = [r for r in top + watch if not r.get("one_liner")]
    if missing:
        for r in missing:
            if r["full_name"] not in readmes:
                get_readme(r)
        enr.summarize(missing, readmes)
        for r in missing:
            if not r.get("one_liner"):
                enr._fallback_texts(r)
    scoring.apply_badges(top, state, cfg, today)

    # 6. historique (snapshot du jour) -----------------------------------------------------------
    for r in short:
        state.record(r, today)
        e = state.entry(r["full_name"])
        e["category"] = r["category"]
        if r.get("one_liner"):
            e.update({"one_liner": r["one_liner"], "details": r.get("details", ""),
                      "fallback": bool(r.get("fallback"))})
    state.record_ranks(extended, cfg["top_n"], today)
    series = {r["full_name"]: state.daily_series(r["full_name"], today, 14) for r in top}

    # 7. textes du jour + rendu ----------------------------------------------------------------------
    cats_in_top = [c for c in cfg["categories"] if any(r["category"] == c["id"] for r in top)]
    enr.daily_texts(top, cats_in_top)
    for r in watch:
        r.setdefault("why_rising", enrich.fallback_why(r))
    shown = [r for r in top if not r.get("stagnant")]
    if len(shown) < len(top):
        log.info("Anti-monotonie : %d projet(s) retiré(s) du message Telegram : %s", len(top) - len(shown),
                 ", ".join(r["full_name"] for r in top if r.get("stagnant")))
    url = page_url(cfg)
    tg = render.build_telegram(shown, cfg, today, url, enr.trends)
    env = render.make_env(ROOT / "templates")
    docs = ROOT / ("out/docs" if args.dry_run else "docs")
    docs.mkdir(parents=True, exist_ok=True)
    (docs / ".nojekyll").touch()
    day_html = render.render_day(env, cfg, today, top, watch, series, now, enr.trends, None, url)
    (docs / f"{today}.html").write_text(day_html, encoding="utf-8")

    week_msgs: list[str] = []
    week_html = None
    if is_sunday:
        week = weekly.build_weekly(state, today, cfg)
        week_html = render.render_weekly(env, cfg, week, today, now, url)
        (docs / f"semaine-{today}.html").write_text(week_html, encoding="utf-8")
        week_msgs = render.build_weekly_telegram(week, cfg, today, f"{url}semaine-{today}.html")
    index_html = render.render_day(env, cfg, today, top, watch, series, now, enr.trends,
                                   render.archive_list(docs), url)
    (docs / "index.html").write_text(index_html, encoding="utf-8")

    # 8. journal ---------------------------------------------------------------------------------------------
    log_top30(extended[:30])
    log.info("Requêtes API GitHub : %d (dont %d Search) sur un budget de %d · appels Claude : %d · durée : %.0f s",
             gh.calls, gh.search_calls, gh.max_requests, enr.llm_calls, time.time() - t0)

    if args.dry_run:
        print("\n" + "=" * 70 + "\nMESSAGE(S) TELEGRAM (aperçu texte — le vrai message est en HTML)\n" + "=" * 70)
        for i, m in enumerate(tg + week_msgs, 1):
            print(f"\n--- message {i}/{len(tg + week_msgs)} ({len(m)} caractères) ---\n{plain_text(m)}")
        print(f"\nPage générée : {docs / 'index.html'}")
        return 0

    # 9. sauvegarde puis envoi ----------------------------------------------------------------------------------
    state.data["last_run"] = today
    removed = state.purge(today, cfg["history_days"])
    state.save(ROOT / "data" / "history.json")
    log.info("history.json sauvegardé (%d dépôts, %d purgés)", len(state.repos), removed)
    if cfg["channel"] == "email":
        notify.send_email(f"AI Radar — {render.fr_date(today)}", index_html)
        if week_html:
            notify.send_email(f"AI Radar — récap de la semaine ({today})", week_html)
    else:
        n = notify.send_telegram(tg + week_msgs)
        log.info("%d message(s) Telegram envoyé(s)", n)
    state.mark_sent(shown, today)
    state.save(ROOT / "data" / "history.json")
    return 0


def log_top30(items: list[dict]) -> None:
    log.info("Top 30 (rang · score · composantes v_abs/v_rel/acc/frais/activ/buzz · 24h/7j · catégorie)")
    for r in items:
        c = r["components"]
        log.info("#%-2d %-42s %.3f [%.2f %.2f %.2f %.2f %.2f %.2f] +%s/+%s %s%s", r["rank"], r["full_name"][:42],
                 r["score"], c["velocity_abs"], c["velocity_rel"], c["acceleration"], c["freshness"],
                 c["activity"], c["buzz"], r["stars_24h"], r["stars_7d"], r["category"],
                 " ⚠susp" if r.get("suspicious") else "")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="ai-radar", description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--dry-run", action="store_true", help="n'envoie rien, ne modifie ni history.json ni docs/")
    ap.add_argument("--top", type=int, help="nombre de projets du classement (défaut : config.yaml)")
    ap.add_argument("--weekly", action="store_true", help="force le récap hebdomadaire")
    ap.add_argument("--force", action="store_true", help="ignore le garde-fou d'horaire du cron")
    ap.add_argument("--date", help="date AAAA-MM-JJ (tests)")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)
    setup_logging(args.verbose)

    cfg = load_config()
    now = datetime.now(timezone.utc)
    paris = now.astimezone(ZoneInfo(cfg["user"]["timezone"]))
    today = args.date or paris.date().isoformat()
    state = State.load(ROOT / "data" / "history.json")

    # Le cron est en UTC : deux déclenchements (heure d'été / d'hiver), un seul doit travailler.
    if should_skip(os.environ.get("GITHUB_EVENT_NAME"), paris.hour, cfg["user"]["send_hour"], state.last_run,
                   today, args.force):
        log.info("Déclenchement ignoré (il est %s à Paris, dernier envoi : %s).", paris.strftime("%H:%M"),
                 state.last_run)
        set_output("skipped", "true")
        return 0
    set_output("skipped", "false")

    is_sunday = args.weekly or date_is_sunday(today)
    try:
        return run(args, cfg, state, now, today, is_sunday)
    except Exception as exc:  # noqa: BLE001
        msg = f"{type(exc).__name__}: {exc}"
        log.exception("Erreur fatale")
        if not args.dry_run:
            notify.send_alert(msg)
        return 1


def should_skip(event: str | None, paris_hour: int, send_hour: int, last_run: str | None, today: str,
                force: bool = False) -> bool:
    """Garde-fou du cron : deux déclenchements UTC (été / hiver), un seul doit travailler.

    Un lancement manuel (workflow_dispatch) ou --force passe toujours.
    """
    if event != "schedule" or force:
        return False
    return paris_hour < send_hour or last_run == today


def date_is_sunday(day: str) -> bool:
    return datetime.fromisoformat(day).weekday() == 6


if __name__ == "__main__":
    sys.exit(main())
