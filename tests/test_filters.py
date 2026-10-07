from datetime import timedelta

from src import filters
from tests.conftest import NOW, iso, make_repo


def kept(cfg, *repos):
    out, stats = filters.apply_filters({r["full_name"]: r for r in repos}, cfg, NOW)
    return [r["full_name"] for r in out], stats


def test_valid_repo_is_kept(cfg):
    names, _ = kept(cfg, make_repo())
    assert names == ["acme/agent-kit"]


def test_license_whitelist(cfg):
    names, stats = kept(cfg, make_repo("a/none", license=None), make_repo("a/noassert", license="NOASSERTION"),
                        make_repo("a/other", license="Other"), make_repo("a/apache", license="Apache-2.0"),
                        make_repo("a/agpl", license="AGPL-3.0"))
    assert sorted(names) == ["a/agpl", "a/apache"]
    assert stats["licence non OSI / absente"] == 3


def test_fork_archived_mirror_and_empty_description(cfg):
    names, stats = kept(cfg, make_repo("a/fork", fork=True), make_repo("a/arch", archived=True),
                        make_repo("a/mirror", mirror=True), make_repo("a/nodesc", description=""))
    assert names == []
    assert stats["fork"] == stats["archivé"] == stats["miroir"] == stats["sans description"] == 1


def test_inactive_repo_is_dropped(cfg):
    old = make_repo("a/old", pushed_at=iso(NOW - timedelta(days=15)))
    fresh = make_repo("a/fresh", pushed_at=iso(NOW - timedelta(days=13)))
    assert kept(cfg, old, fresh)[0] == ["a/fresh"]


def test_awesome_lists_and_prompt_collections_are_excluded(cfg):
    repos = [
        make_repo("a/awesome-llm", description="A curated list of LLM resources"),
        make_repo("a/llm-awesome-list", topics=["llm", "awesome-list"]),
        make_repo("a/system-prompts", description="Collection of system prompts for AI"),
        make_repo("a/ai-course", description="Free course to learn AI from scratch"),
        make_repo("a/real", description="Awesome fast LLM inference server"),  # « awesome » dans une vraie description
    ]
    assert kept(cfg, *repos)[0] == ["a/real"]


def test_exclusion_can_be_disabled(cfg):
    cfg2 = {**cfg, "filters": {**cfg["filters"], "exclude_lists": False}}
    assert kept(cfg2, make_repo("a/awesome-llm"))[0] == ["a/awesome-llm"]


def test_ai_relevance_by_topic_keyword_and_borderline(cfg):
    assert filters.ai_relevance(make_repo(topics=["rag"], description="x"), cfg) == "topic"
    assert filters.ai_relevance(make_repo(topics=[], description="Run LLMs locally"), cfg) == "strong"
    assert filters.ai_relevance(make_repo(topics=[], description="A web framework with agents"), cfg) == "weak"
    assert filters.ai_relevance(make_repo(topics=["web"], description="A static site generator"), cfg) is None


def test_non_ai_repo_dropped_and_borderline_flagged(cfg):
    names, stats = kept(cfg, make_repo("a/blog", topics=["web"], description="A static site generator"),
                        make_repo("a/maybe", topics=[], description="Chat agents for your team"))
    assert names == ["a/maybe"] and stats["hors IA"] == 1


def test_readme_confirmation(cfg):
    assert filters.readme_confirms_ai("This LLM tool uses OpenAI embeddings and RAG", cfg)
    assert not filters.readme_confirms_ai("A calendar app", cfg)
    assert not filters.readme_confirms_ai(None, cfg)


def test_suspicious_stars(cfg):
    assert filters.is_suspicious(make_repo(stars=5000, forks=5), cfg)
    assert not filters.is_suspicious(make_repo(stars=5000, forks=500), cfg)
    assert not filters.is_suspicious(make_repo(stars=900, forks=0), cfg)
