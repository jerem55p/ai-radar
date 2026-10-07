from src import collect

TRENDING_HTML = """
<html><body>
<article class="Box-row">
  <h2 class="h3 lh-condensed"><a href="/acme/agent-kit"> acme / agent-kit </a></h2>
  <p class="col-9 color-fg-muted my-1 pr-4">Build agents fast</p>
  <span itemprop="programmingLanguage">Python</span>
  <a href="/acme/agent-kit/stargazers">12,345</a><a href="/acme/agent-kit/forks">678</a>
  <span class="d-inline-block float-sm-right">1,234 stars today</span>
</article>
<article class="Box-row"><h2><a href="/x/y"> x / y </a></h2><span>56 stars this week</span></article>
<article class="Box-row"><p>pas de lien</p></article>
</body></html>
"""


def test_parse_trending_extracts_names_and_star_gains():
    items = collect.parse_trending(TRENDING_HTML, "daily")
    assert [i["full_name"] for i in items] == ["acme/agent-kit", "x/y"]
    first = items[0]
    assert first["stars_period"] == 1234 and first["stars_total"] == 12345 and first["language"] == "Python"
    assert first["description"] == "Build agents fast"
    assert items[1]["stars_period"] == 56


def test_trending_failure_does_not_raise(cfg, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("GitHub a changé sa page")

    monkeypatch.setattr(collect, "plain_get", boom)
    monkeypatch.setattr(collect.time, "sleep", lambda s: None)
    assert collect.collect_trending(cfg) == {}


def test_trending_html_change_yields_empty_not_crash(cfg, monkeypatch):
    class R:
        status_code = 200
        text = "<html><body><div>nouvelle mise en page</div></body></html>"

    monkeypatch.setattr(collect, "plain_get", lambda *a, **k: R())
    monkeypatch.setattr(collect.time, "sleep", lambda s: None)
    assert collect.collect_trending(cfg) == {}


def test_trending_numbers_are_merged_by_period(cfg, monkeypatch):
    class R:
        status_code = 200
        text = TRENDING_HTML

    monkeypatch.setattr(collect, "plain_get", lambda *a, **k: R())
    monkeypatch.setattr(collect.time, "sleep", lambda s: None)
    out = collect.collect_trending({**cfg, "collect": {**cfg["collect"], "trending": {
        **cfg["collect"]["trending"], "languages": [""], "periods": ["daily", "weekly"]}}})
    assert out["acme/agent-kit"]["trend_day"] == 1234


def test_normalize_repo_handles_missing_license():
    item = {"full_name": "a/b", "owner": {"login": "a"}, "license": None, "stargazers_count": 5, "topics": None}
    r = collect.normalize_repo(item, "search")
    assert r["license"] is None and r["topics"] == [] and r["owner"] == "a" and r["sources"] == {"search"}
    r2 = collect.normalize_repo({**item, "license": {"spdx_id": "MIT"}, "mirror_url": "https://x"}, "search")
    assert r2["license"] == "MIT" and r2["mirror"]
