import re

from src import render
from tests.conftest import make_repo


def fake_top(cfg, n=20, one_len=12):
    cats = [c["id"] for c in cfg["categories"]]
    top = []
    for i in range(n):
        top.append(make_repo(f"owner{i}/project-{i}", owner=f"owner{i}", category=cats[i % len(cats)], rank=i + 1,
                             stars=12_345 + i, stars_24h=1850 - i * 50, hn_points=0,
                             one_liner=" ".join(["mot"] * one_len), is_new=(i % 2 == 0), rank_delta=3 if i == 4 else 0,
                             details="Première phrase. Deuxième phrase. Troisième phrase."))
    return top


def test_number_formats():
    assert render.fmt_k(850) == "850" and render.fmt_k(12_345) == "12,3k" and render.fmt_k(12_000) == "12k"
    assert render.fmt_k(250_000) == "250k"
    assert render.fmt_int(1850) == "1 850"
    assert render.fr_date("2026-10-02") == "vendredi 2 octobre 2026"
    assert render.fr_date("2026-11-01", weekday=False) == "1er novembre 2026"


def test_badges_text():
    r = make_repo(is_new=True, rank_delta=-2, repeat=True, hn_badge=True, hn_points=412)
    assert render.badge_parts(r) == ["🆕", "↓2", "🔁", "🔥HN 412"]
    assert render.badge_parts(make_repo(hn_points=3, hn_badge=False)) == []


def test_telegram_fits_limits_and_structure(cfg):
    msgs = render.build_telegram(fake_top(cfg), cfg, "2026-10-02", "https://u.github.io/ai-radar/", {})
    assert 1 <= len(msgs) <= 3
    assert all(len(m) <= 4096 for m in msgs)
    assert msgs[0].startswith("🤖 <b>AI Radar</b> — vendredi 2 octobre 2026")
    assert "🏆 <b>PODIUM</b>" in msgs[0]
    assert "https://u.github.io/ai-radar/" in msgs[-1]
    full = "\n".join(msgs)
    assert full.count("<a href=") == 20  # chaque projet apparaît une seule fois, cliquable
    assert re.search(r"#4 <a href=\"https://github.com/owner3/project-3\">owner3/project-3</a>", full)
    assert "↑3" in full


def test_podium_projects_not_repeated_in_categories(cfg):
    msgs = render.build_telegram(fake_top(cfg), cfg, "2026-10-02", "u", {})
    full = "\n".join(msgs)
    for i in range(3):
        assert full.count(f">owner{i}/project-{i}<") == 1


def test_empty_categories_are_hidden(cfg):
    top = [r for r in fake_top(cfg) if r["category"] in ("agents", "dev")]
    for i, r in enumerate(top, 1):
        r["rank"] = i
    full = "\n".join(render.build_telegram(top, cfg, "2026-10-02", "u", {}))
    assert "AGENTS &amp; AUTOMATISATION" in full.replace("&amp;", "&amp;") or "AGENTS & AUTOMATISATION" in full
    assert "AUDIO" not in full and "IMAGE" not in full


def test_split_never_cuts_a_category(cfg):
    blocks = [f"{i} " + "x" * 1500 for i in range(5)]
    msgs = render.pack_blocks(blocks, limit=4096)
    assert all(len(m) <= 4096 for m in msgs)
    joined = "\n\n".join(msgs)
    assert all(b in joined for b in blocks)
    for b in blocks:  # chaque bloc est entier dans un seul message
        assert sum(b in m for m in msgs) == 1


def test_oversized_input_is_compressed_to_three_messages(cfg):
    top = fake_top(cfg, one_len=60)
    for r in top:
        r["one_liner"] = " ".join(["longueur"] * 60)
        r["details"] = " ".join(["Une phrase très longue."] * 40)
    trends = {c["id"]: "Tendance " + "bla " * 30 for c in cfg["categories"]}
    msgs = render.build_telegram(top, cfg, "2026-10-02", "u", trends)
    assert len(msgs) <= 3 and all(len(m) <= 4096 for m in msgs)


def test_html_is_escaped_in_telegram(cfg):
    top = fake_top(cfg, n=5)
    top[4]["one_liner"] = "Utilise <script> & co"
    full = "\n".join(render.build_telegram(top, cfg, "2026-10-02", "u", {}))
    assert "<script>" not in full and "&lt;script&gt;" in full


def test_sparkline_svg():
    assert "svg" in render.sparkline([1, 5, None, 9, 3])
    assert "historique en construction" in render.sparkline([None, 4])


def test_day_page_renders_with_filters_and_archives(cfg, now, tmp_path):
    from pathlib import Path
    env = render.make_env(Path(__file__).resolve().parent.parent / "templates")
    top = fake_top(cfg)
    series = {r["full_name"]: [1, 2, 3, None, 5] * 3 for r in top}
    page = render.render_day(env, cfg, "2026-10-02", top, [], series, now, {"agents": "Tendance"},
                             [{"file": "2026-10-01.html", "label": "jeudi", "kind": "day"}], "https://u/")
    assert page.count("<article") == 20 and 'data-filter="agents"' in page and "<svg" in page
    assert "2026-10-01.html" in page and "prefers-color-scheme" in page
    assert 'name="viewport"' in page


# ------------------------------------------------------------ orchestration ----
def test_cron_guard_summer_and_winter():
    from src.main import should_skip

    # été : 06:00 UTC = 8 h Paris → travaille ; 07:00 UTC = 9 h Paris, déjà envoyé → ignore
    assert not should_skip("schedule", 8, 8, "2026-10-05", "2026-10-06")
    assert should_skip("schedule", 9, 8, "2026-10-06", "2026-10-06")
    # hiver : 06:00 UTC = 7 h Paris → trop tôt, ignore ; 07:00 UTC = 8 h → travaille
    assert should_skip("schedule", 7, 8, "2026-01-05", "2026-01-06")
    assert not should_skip("schedule", 8, 8, "2026-01-05", "2026-01-06")
    # cron retardé par GitHub (10 h) mais rien d'envoyé aujourd'hui → travaille quand même
    assert not should_skip("schedule", 10, 8, "2026-10-05", "2026-10-06")
    # lancement manuel : toujours
    assert not should_skip("workflow_dispatch", 3, 8, "2026-10-06", "2026-10-06")
    assert not should_skip("schedule", 3, 8, "2026-10-06", "2026-10-06", force=True)


def test_secrets_are_scrubbed_from_logs(monkeypatch):
    import logging

    from src.main import SecretScrubber

    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123456:SUPER-SECRET-VALUE")
    rec = logging.LogRecord("x", logging.INFO, "f", 1, "url https://api.telegram.org/bot%s/send", ("123456:SUPER-SECRET-VALUE",), None)
    assert SecretScrubber().filter(rec)
    assert "SUPER-SECRET" not in rec.getMessage() and "***" in rec.getMessage()


def test_code_contains_no_hardcoded_secret():
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    for p in list((root / "src").glob("*.py")) + [root / "config.yaml", root / ".github/workflows/daily.yml"]:
        text = p.read_text(encoding="utf-8")
        assert not re.search(r"\b\d{8,10}:[A-Za-z0-9_-]{30,}\b", text), p  # token Telegram
        assert not re.search(r"sk-ant-[A-Za-z0-9_-]{20,}|ghp_[A-Za-z0-9]{30,}|github_pat_\w{30,}", text), p


def test_weekly_page_and_message(cfg, now):
    from pathlib import Path

    week = {"start": "2026-10-05", "enough_history": True, "top_category": {"id": "agents", "gain": 4200},
            "top10": [{"full_name": "a/b", "html_url": "https://github.com/a/b", "category": "agents", "one_liner": "Fait X",
                       "gain_7d": 4200, "days_in_top": 5}],
            "confirmed": [{"full_name": "a/b", "html_url": "https://github.com/a/b", "category": "agents", "one_liner": "",
                           "gain_7d": 4200, "days_in_top": 5}],
            "flashes": []}
    env = render.make_env(Path(__file__).resolve().parent.parent / "templates")
    page = render.render_weekly(env, cfg, week, "2026-10-11", now, "https://u/")
    assert "Top 10 de la semaine" in page and "Se confirment" in page and "a/b" in page
    msgs = render.build_weekly_telegram(week, cfg, "2026-10-11", "https://u/semaine.html")
    assert len(msgs) == 1 and "TOP 10 DE LA SEMAINE" in msgs[0] and "Se confirment" in msgs[0]
    assert "Catégorie la plus active" in msgs[0]
