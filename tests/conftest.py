import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

NOW = datetime(2026, 10, 6, 12, 0, tzinfo=timezone.utc)


@pytest.fixture(scope="session")
def cfg():
    return yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))


@pytest.fixture
def now():
    return NOW


def iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def make_repo(name="acme/agent-kit", **kw):
    """Dépôt factice, déjà normalisé (mêmes clés que collect.normalize_repo + mesures)."""
    owner = name.split("/")[0]
    base = {
        "full_name": name, "owner": owner, "html_url": f"https://github.com/{name}",
        "description": "An AI agent framework for LLM apps", "topics": ["llm", "ai-agents"],
        "language": "Python", "license": "MIT", "stars": 2000, "forks": 200, "open_issues": 30,
        "created_at": iso(NOW - timedelta(days=20)), "pushed_at": iso(NOW - timedelta(hours=5)),
        "fork": False, "archived": False, "mirror": False, "sources": {"search"},
        "stars_24h": 300, "stars_7d": 900, "stars_7d_known": True, "commits_7d": 20,
        "release_recent": False, "hn_points": 0, "hn_badge": False, "category": "agents",
        "score": 0.5, "age_days": 20.0, "one_liner": "Construit des agents", "details": "Deux phrases.",
        "why_rising": "Beaucoup d'étoiles.",
    }
    base.update(kw)
    return base
