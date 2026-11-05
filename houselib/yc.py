"""Search the Y Combinator company directory (unofficial daily snapshot by yc-oss, from YC's own index)."""
import json
import re
import time
import urllib.request
from pathlib import Path

URL = "https://yc-oss.github.io/api/companies/all.json"
CACHE = Path.home() / ".cache/house/yc-companies.json"
MAX_AGE_S = 86400
STOPWORDS = {"a", "an", "the", "and", "or", "for", "of", "to", "in", "on", "with", "that", "you", "your", "by", "is"}
WEIGHTS = {"name": 2, "one_liner": 3, "tags": 2, "industries": 1, "long_description": 1}


def _fetch(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "house-yc-search"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read()


def load(cache: Path = CACHE, fetch=_fetch, max_age_s: int = MAX_AGE_S) -> list[dict]:
    """Companies from the cache; refetch when older than max_age_s; fall back to a stale cache when offline."""
    fresh = cache.exists() and time.time() - cache.stat().st_mtime < max_age_s
    if not fresh:
        try:
            data = fetch(URL)
            json.loads(data)  # never cache garbage
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_bytes(data)
        except (OSError, ValueError):
            if not cache.exists():
                raise
    return json.loads(cache.read_text())


def _terms(text: str) -> list[str]:
    return [t for t in re.findall(r"[a-z0-9]+", text.lower()) if t not in STOPWORDS]


def _field_text(company: dict, field: str) -> str:
    value = company.get(field)
    if isinstance(value, list):
        return " ".join(str(v) for v in value)
    return str(value or "")


def _stem(term: str) -> str:
    return term[:-1] if len(term) > 3 and term.endswith("s") else term


def score(company: dict, terms: list[str]) -> int:
    total = 0
    for field, weight in WEIGHTS.items():
        words = {_stem(w) for w in _terms(_field_text(company, field))}
        total += weight * sum(1 for t in terms if _stem(t) in words)
    return total


def search(companies: list[dict], query: str, limit: int = 15) -> list[dict]:
    terms = _terms(query)
    scored = [(score(c, terms), c) for c in companies]
    hits = [(s, c) for s, c in scored if s > 0]
    hits.sort(key=lambda sc: -sc[0])
    if not hits:
        return []
    best = hits[0][0]
    return [c for s, c in hits if s * 3 >= best][:limit]  # drop weak tail matches


def format_hit(c: dict) -> str:
    return f"- {c.get('name')} ({c.get('batch')}, {c.get('status')}): {c.get('one_liner') or ''} — {c.get('url')}"
