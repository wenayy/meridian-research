"""Source credibility scoring and near-duplicate detection.

The score (0..1) is a transparent, explainable prior, not a truth oracle:
  * domain type      .gov / .edu / multilaterals / standards bodies > established outlets
                      > company pages (self-interested) > unknown blogs > content farms
  * recency          stale sources lose points for fast-moving topics
  * corroboration    applied later: claims seen across independent domains gain weight

Every adjustment is recorded in ``reasons`` so the report can show *why* a source was trusted.
"""
from __future__ import annotations

import re
from datetime import date
from urllib.parse import urlparse

HIGH = (".gov", ".gov.in", ".nic.in", ".edu", ".ac.in", ".ac.uk", ".int", "who.int", "worldbank.org", "imf.org",
        "oecd.org", "iea.org", "nature.com", "science.org", "arxiv.org", "rbi.org.in", "sebi.gov.in")
ESTABLISHED = ("reuters.com", "apnews.com", "bloomberg.com", "ft.com", "economist.com", "wsj.com", "bbc.co.uk",
               "bbc.com", "thehindu.com", "livemint.com", "economictimes.indiatimes.com", "techcrunch.com",
               "wikipedia.org", "mckinsey.com", "gartner.com")
LOW_SIGNALS = ("best-", "top-10", "top10", "review-", "deals", "coupon", "-hacks", "clickbait")


def domain_of(url: str) -> str:
    host = urlparse(url).netloc.lower()
    return host[4:] if host.startswith("www.") else host


def score_source(url: str, title: str = "", published: str | None = None, today: date | None = None,
                 extra_high: tuple[str, ...] = (), extra_low: tuple[str, ...] = ()) -> tuple[float, list[str]]:
    d = domain_of(url)
    score, reasons = 0.5, []
    if d.endswith(HIGH + extra_high) or d in HIGH + extra_high:
        score, reasons = 0.9, ["government / academic / multilateral domain"]
    elif any(d.endswith(e) for e in ESTABLISHED):
        score, reasons = 0.75, ["established publication"]
    elif re.search(r"(^|\.)(blog|medium\.com|substack\.com|wordpress|blogspot)", d):
        score, reasons = 0.4, ["personal blog / newsletter platform"]
    if any(s in (url + " " + title).lower() for s in LOW_SIGNALS) or d.endswith(extra_low):
        score -= 0.25
        reasons.append("listicle / affiliate / content-farm signals")
    if "press" in url.lower() or "newsroom" in url.lower():
        score -= 0.1
        reasons.append("press release (self-interested)")
    if published:
        m = re.match(r"(\d{4})", published)
        if m:
            age = (today or date.today()).year - int(m.group(1))
            if age >= 4:
                score -= 0.15
                reasons.append(f"published {age} years ago")
            elif age <= 1:
                score += 0.05
                reasons.append("recent")
    return round(min(max(score, 0.05), 0.98), 2), reasons or ["no strong signals"]


def shingles(text: str, n: int = 5) -> set[int]:
    words = re.findall(r"\w+", text.lower())
    return {hash(" ".join(words[i:i + n])) for i in range(max(len(words) - n + 1, 1))}


def jaccard(a: set[int], b: set[int]) -> float:
    return len(a & b) / len(a | b) if a and b else 0.0


def is_near_duplicate(text: str, others: list[set[int]], threshold: float = 0.6) -> bool:
    """Syndicated copies of the same article shouldn't count as independent corroboration."""
    s = shingles(text)
    return any(jaccard(s, o) >= threshold for o in others)
