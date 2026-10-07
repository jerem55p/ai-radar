from datetime import date, timedelta

from src import scoring
from src.state import State
from tests.conftest import make_repo


def day(n: int, base="2026-10-10") -> str:
    return (date.fromisoformat(base) - timedelta(days=n)).isoformat()


def history(full_name, *, top20_days=(), ranks=None, daily=None):
    st = State()
    e = st.entry(full_name)
    e["top20"] = [day(n) for n in top20_days]
    e["ranks"] = {day(n): rk for n, rk in (ranks or {}).items()}
    e["daily"] = {day(n): v for n, v in (daily or {}).items()}
    return st


def test_new_badge_when_never_in_top20(cfg):
    r = make_repo("a/new")
    scoring.apply_badges([{**r, "rank": 3}], State(), cfg, day(0))
    top = [{**r, "rank": 3}]
    scoring.apply_badges(top, State(), cfg, day(0))
    assert top[0]["is_new"] and not top[0]["repeat"] and top[0]["rank_delta"] == 0


def test_rank_delta_up_and_down(cfg):
    st = history("a/up", top20_days=[1], ranks={1: 9})
    st.entry("b/down")["ranks"] = {day(1): 2}
    st.entry("b/down")["top20"] = [day(1)]
    top = [{**make_repo("a/up"), "rank": 6}, {**make_repo("b/down"), "rank": 4}]
    scoring.apply_badges(top, st, cfg, day(0))
    assert top[0]["rank_delta"] == 3 and not top[0]["is_new"]  # ↑3
    assert top[1]["rank_delta"] == -2  # ↓2


def test_repeat_badge_after_three_consecutive_days(cfg):
    st = history("a/loyal", top20_days=[1, 2], ranks={1: 5, 2: 5})
    top = [{**make_repo("a/loyal"), "rank": 5}]
    scoring.apply_badges(top, st, cfg, day(0))
    assert top[0]["streak"] == 3 and top[0]["repeat"]
    st2 = history("a/gap", top20_days=[1, 3], ranks={1: 5})  # un trou : la série repart à 2
    top2 = [{**make_repo("a/gap"), "rank": 5}]
    scoring.apply_badges(top2, st2, cfg, day(0))
    assert top2[0]["streak"] == 2 and not top2[0]["repeat"]


def test_stagnation_needs_long_streak_and_declines(cfg):
    days = list(range(1, 9))  # 8 jours déjà dans le top → série = 9 > 7
    declining = history("a/old", top20_days=days, ranks={1: 5}, daily={3: 400, 2: 300, 1: 200})
    top = [{**make_repo("a/old", stars_24h=100), "rank": 5}]
    scoring.apply_badges(top, declining, cfg, day(0))
    assert top[0]["stagnant"]

    rising = history("a/hot", top20_days=days, ranks={1: 5}, daily={3: 100, 2: 300, 1: 200})
    top = [{**make_repo("a/hot", stars_24h=100), "rank": 5}]
    scoring.apply_badges(top, rising, cfg, day(0))
    assert not top[0]["stagnant"]

    short = history("a/young", top20_days=[1, 2, 3], ranks={1: 5}, daily={3: 400, 2: 300, 1: 200})
    top = [{**make_repo("a/young", stars_24h=100), "rank": 5}]
    scoring.apply_badges(top, short, cfg, day(0))
    assert not top[0]["stagnant"]  # pas encore plus de 7 jours d'affilée


def test_stars_diff_scales_over_gap():
    st = State()
    st.entry("a/b")["stars"] = {day(2): 1000}
    assert st.stars_diff("a/b", day(0), 1, 1300) == 150  # 300 étoiles en 2 jours → 150 / jour
    assert st.stars_diff("x/none", day(0), 1, 1300) is None


def test_purge_removes_old_data(cfg):
    st = State()
    e = st.entry("a/old")
    e["stars"] = {day(100): 5}
    e["last_seen"] = day(100)
    e2 = st.entry("a/live")
    e2["stars"] = {day(100): 5, day(1): 50}
    e2["last_seen"] = day(1)
    removed = st.purge(day(0), 60)
    assert removed == 1 and "a/old" not in st.repos
    assert list(st.repos["a/live"]["stars"]) == [day(1)]


def test_record_and_roundtrip(tmp_path):
    st = State()
    r = make_repo("a/b", daily_backfill={day(2): 7, day(0): 99})
    st.record(r, day(0))
    st.record_ranks([{"full_name": "a/b"}], 20, day(0))
    st.mark_sent([r], day(0))
    p = tmp_path / "h.json"
    st.save(p)
    st2 = State.load(p)
    e = st2.get("a/b")
    assert e["stars"][day(0)] == 2000 and e["daily"][day(2)] == 7 and e["daily"][day(0)] == 300
    assert e["top20"] == [day(0)] and e["sent"] == [day(0)] and e["ranks"][day(0)] == 1
    assert State.load(tmp_path / "missing.json").repos == {}


def test_followed_returns_recent_top60():
    st = State()
    st.entry("a/in")["ranks"] = {day(2): 40}
    st.entry("b/out")["ranks"] = {day(2): 90}
    st.entry("c/old")["ranks"] = {day(20): 3}
    assert st.followed(day(0), 7, 60) == ["a/in"]
