import math

from src import scoring
from tests.conftest import make_repo


def ages(repos):
    return {r["full_name"]: r["age_days"] for r in repos}


def test_normalize_bounds_and_constant():
    assert scoring.normalize([1, 2, 3]) == [0.0, 0.5, 1.0]
    assert scoring.normalize([5, 5]) == [0.0, 0.0]
    assert scoring.normalize([]) == []


def test_normalize_clips_outliers():
    out = scoring.normalize([1, 2, 3, 4, 1000], clip_pct=0.5)
    assert max(out) == 1.0 and out[2] == 1.0  # le percentile 50 vaut 3 : tout ce qui dépasse est écrêté


def test_higher_velocity_ranks_first(cfg):
    a = make_repo("a/hot", stars_24h=900, stars_7d=1000, stars=3000)
    b = make_repo("b/calm", stars_24h=50, stars_7d=400, stars=3000)
    ranked = scoring.score_all([b, a], cfg, ages([a, b]))
    assert [r["full_name"] for r in ranked] == ["a/hot", "b/calm"]
    assert 0 <= ranked[0]["score"] <= 1


def test_weights_are_configurable(cfg):
    a = make_repo("a/viral", stars_24h=500, stars=600, stars_7d=500, hn_points=0)
    b = make_repo("b/buzz", stars_24h=100, stars=9000, stars_7d=700, hn_points=500)
    weights = {k: 0.0 for k in cfg["score"]["weights"]}
    weights["buzz"] = 1.0
    only_buzz = {**cfg, "score": {**cfg["score"], "weights": weights}}
    assert scoring.score_all([a, b], only_buzz, ages([a, b]))[0]["full_name"] == "b/buzz"


def test_suspicious_penalty_halves_score(cfg):
    a = make_repo("a/clean")
    b = make_repo("b/fake", suspicious=True)
    c = make_repo("c/other", stars_24h=10, stars_7d=70)
    ranked = {r["full_name"]: r for r in scoring.score_all([a, b, c], cfg, ages([a, b, c]))}
    assert math.isclose(ranked["b/fake"]["score"], ranked["a/clean"]["score"] * 0.5)


def test_unknown_7d_uses_neutral_acceleration(cfg):
    known = [make_repo(f"k/r{i}", stars_24h=100 * (i + 1), stars_7d=300 * (i + 1)) for i in range(3)]
    unknown = make_repo("u/new", stars_24h=100, stars_7d=100, stars_7d_known=False)
    out = scoring.score_all(known + [unknown], cfg, ages(known + [unknown]))
    comp = {r["full_name"]: r["components"]["acceleration"] for r in out}
    assert comp["u/new"] < 1.0  # sans 7 jours connus : pas de faux bonus d'accélération maximal


def test_rule_category_from_topics(cfg):
    assert scoring.rule_category(make_repo(topics=["text-to-speech"], description="x"), cfg) == "audio"
    assert scoring.rule_category(make_repo(topics=["rag", "vector-database"], description="x"), cfg) == "rag"
    assert scoring.rule_category(make_repo(topics=[], description="zzz"), cfg) == cfg["default_category"]


# ------------------------------------------------------------ quotas ----
def many(n, cat, owner_prefix, start_score):
    return [make_repo(f"{owner_prefix}{i}/{cat}-{i}", category=cat, owner=f"{owner_prefix}{i}",
                      score=start_score - i * 0.01) for i in range(n)]


def test_max_five_per_category(cfg):
    ranked = many(12, "agents", "a", 0.9) + many(12, "dev", "d", 0.5)
    ranked.sort(key=lambda r: -r["score"])
    top = scoring.distribute(ranked, cfg)
    assert len(top) == 10  # seulement 5 + 5 projets éligibles sous le plafond
    counts = {}
    for r in top:
        counts[r["category"]] = counts.get(r["category"], 0) + 1
    assert max(counts.values()) <= 5


def test_category_cap_overflow_goes_to_next_in_global_ranking(cfg):
    ranked = many(8, "agents", "a", 0.95) + many(15, "dev", "d", 0.60) + many(15, "rag", "r", 0.40)
    ranked += many(15, "apps", "p", 0.30)
    ranked.sort(key=lambda r: -r["score"])
    top = scoring.distribute(ranked, cfg)
    cats = [r["category"] for r in top]
    assert cats.count("agents") == cats.count("dev") == cats.count("rag") == cats.count("apps") == 5
    assert len(top) == 20
    assert [r["rank"] for r in top] == list(range(1, 21))
    assert all(top[i]["score"] >= top[i + 1]["score"] for i in range(19))


def test_max_two_per_owner(cfg):
    ranked = [make_repo(f"big/r{i}", owner="big", category=c, score=0.9 - i * 0.01)
              for i, c in enumerate(["agents", "dev", "rag", "audio"])]
    ranked += [make_repo(f"o{i}/x", owner=f"o{i}", category="apps", score=0.5 - i * 0.01) for i in range(5)]
    ranked.sort(key=lambda r: -r["score"])
    top = scoring.distribute(ranked, cfg)
    assert sum(1 for r in top if r["owner"] == "big") == 2


def _crowded():
    ranked = many(14, "agents", "a", 0.90) + many(10, "dev", "d", 0.88) + many(10, "rag", "r", 0.87)
    ranked += many(10, "apps", "p", 0.86)
    ranked.sort(key=lambda r: -r["score"])
    return ranked


def test_rescue_rule_gives_empty_category_a_slot(cfg):
    ranked = _crowded()
    twentieth = scoring.distribute(list(ranked), cfg)[-1]["score"]
    img = make_repo("zed/img", owner="zed", category="image", score=twentieth * 0.7)
    top = scoring.distribute(sorted(ranked + [img], key=lambda r: -r["score"]), cfg)
    assert "zed/img" in [r["full_name"] for r in top]
    assert len(top) == 20


def test_rescue_rule_ignores_candidate_below_threshold(cfg):
    ranked = _crowded()
    twentieth = scoring.distribute(list(ranked), cfg)[-1]["score"]
    img = make_repo("zed/img", owner="zed", category="image", score=twentieth * 0.5)
    top = scoring.distribute(sorted(ranked + [img], key=lambda r: -r["score"]), cfg)
    assert "zed/img" not in [r["full_name"] for r in top]


def test_extended_list_keeps_rest_ranked(cfg):
    ranked = [make_repo(f"o{i}/r{i}", owner=f"o{i}", category=c, score=0.9 - i * 0.01)
              for i, c in enumerate(["agents", "dev", "rag", "audio", "apps", "image"] * 6)]
    top = scoring.distribute(ranked, cfg)
    ext = scoring.extended_list(top, ranked)
    assert len(ext) == 36 and [r["rank"] for r in ext] == list(range(1, 37))
