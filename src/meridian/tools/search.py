"""Pluggable web search.

Providers: Tavily, Brave, Serper (Google) and DuckDuckGo (no key), plus ``LocalCorpus``,
an offline "synthetic web" used for tests, demos and the benchmark. All return the
same ``SearchResult`` shape, and all are wrapped by a cache.
"""
from __future__ import annotations

import json
import math
import re
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Protocol
from urllib.parse import parse_qs, unquote, urlparse

from ..tracing import span
from .cache import Cache


@dataclass
class SearchResult:
    url: str
    title: str
    snippet: str
    published: str | None = None


class SearchProvider(Protocol):
    name: str

    def search(self, query: str, k: int = 5) -> list[SearchResult]: ...


def _http():
    import httpx

    return httpx.Client(timeout=20, headers={"User-Agent": "Mozilla/5.0 (Meridian research agent)"},
                        follow_redirects=True)


class TavilySearch:
    name = "tavily"

    def __init__(self, api_key: str):
        self.api_key = api_key

    def search(self, query: str, k: int = 5) -> list[SearchResult]:
        with _http() as c:
            r = c.post("https://api.tavily.com/search", json={"api_key": self.api_key, "query": query, "max_results": k})
            r.raise_for_status()
        return [SearchResult(x["url"], x.get("title", ""), x.get("content", ""), x.get("published_date"))
                for x in r.json().get("results", [])]


class BraveSearch:
    name = "brave"

    def __init__(self, api_key: str):
        self.api_key = api_key

    def search(self, query: str, k: int = 5) -> list[SearchResult]:
        with _http() as c:
            r = c.get("https://api.search.brave.com/res/v1/web/search", params={"q": query, "count": k},
                      headers={"X-Subscription-Token": self.api_key, "Accept": "application/json"})
            r.raise_for_status()
        return [SearchResult(x["url"], x.get("title", ""), re.sub("<[^>]+>", "", x.get("description", "")), x.get("age"))
                for x in r.json().get("web", {}).get("results", [])[:k]]


class SerperSearch:
    name = "serper"

    def __init__(self, api_key: str):
        self.api_key = api_key

    def search(self, query: str, k: int = 5) -> list[SearchResult]:
        with _http() as c:
            r = c.post("https://google.serper.dev/search", json={"q": query, "num": k},
                       headers={"X-API-KEY": self.api_key})
            r.raise_for_status()
        return [SearchResult(x["link"], x.get("title", ""), x.get("snippet", ""), x.get("date"))
                for x in r.json().get("organic", [])[:k]]


class DuckDuckGoSearch:
    """Key-less HTML endpoint. Fine for demos; use a paid API for anything serious."""

    name = "duckduckgo"

    def search(self, query: str, k: int = 5) -> list[SearchResult]:
        from bs4 import BeautifulSoup

        with _http() as c:
            r = c.post("https://html.duckduckgo.com/html/", data={"q": query})
            r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        out = []
        for res in soup.select(".result")[: k * 2]:
            a = res.select_one("a.result__a")
            if not a:
                continue
            href = a.get("href", "")
            if "uddg=" in href:  # DDG redirect wrapper
                href = unquote(parse_qs(urlparse(href).query).get("uddg", [href])[0])
            snip = res.select_one(".result__snippet")
            out.append(SearchResult(href, a.get_text(" ", strip=True), snip.get_text(" ", strip=True) if snip else ""))
            if len(out) >= k:
                break
        return out


# ---------------------------------------------------------------- offline synthetic web

_TOK = re.compile(r"[a-z0-9]+(?:[.,][0-9]+)*")
_STOP = frozenset("the a an of and or in on to for is are was were by with what which who how does do at as from".split())


def _toks(t: str) -> list[str]:
    return [w.replace(",", "") for w in _TOK.findall(t.lower()) if w not in _STOP]


class LocalCorpus:
    """BM25 search over a JSONL file of pages: {url, title, text, published}."""

    name = "local"

    def __init__(self, path: Path | str):
        self.pages = [json.loads(line) for line in Path(path).open() if line.strip()]
        self._post: dict[str, list[tuple[int, int]]] = defaultdict(list)
        self._len = []
        for i, p in enumerate(self.pages):
            tf = Counter(_toks(p["title"] + " " + p["title"] + " " + p["text"]))
            self._len.append(sum(tf.values()))
            for t, c in tf.items():
                self._post[t].append((i, c))
        self._avg = sum(self._len) / max(len(self._len), 1)
        n = len(self.pages)
        self._idf = {t: math.log(1 + (n - len(p) + 0.5) / (len(p) + 0.5)) for t, p in self._post.items()}

    def page(self, url: str) -> dict | None:
        return next((p for p in self.pages if p["url"] == url), None)

    def search(self, query: str, k: int = 5) -> list[SearchResult]:
        scores: dict[int, float] = defaultdict(float)
        for t in set(_toks(query)):
            for i, tf in self._post.get(t, []):
                norm = 1.5 * (0.25 + 0.75 * self._len[i] / self._avg)
                scores[i] += self._idf[t] * tf * 2.5 / (tf + norm)
        top = sorted(scores.items(), key=lambda x: -x[1])[:k]
        return [SearchResult(self.pages[i]["url"], self.pages[i]["title"], self.pages[i]["text"][:240],
                             self.pages[i].get("published")) for i, _ in top]


# ---------------------------------------------------------------- caching wrapper


class CachedSearch:
    def __init__(self, provider: SearchProvider, cache: Cache):
        self.provider, self.cache, self.name = provider, cache, provider.name

    def search(self, query: str, k: int = 5) -> list[SearchResult]:
        key = Cache.key("search", self.provider.name, query, k)
        with span("tool.search", provider=self.provider.name, query=query) as s:
            hit = self.cache.get(key)
            if hit is not None:
                s.set(cached=True, results=len(hit))
                return [SearchResult(**x) for x in hit]
            res = self.provider.search(query, k)
            self.cache.set(key, [asdict(r) for r in res])
            s.set(cached=False, results=len(res))
            return res


def make_search(provider: str, api_key: str | None = None, corpus: Path | None = None) -> SearchProvider:
    if provider == "local":
        return LocalCorpus(corpus or Path("benchmark/corpus.jsonl"))
    if provider == "duckduckgo":
        return DuckDuckGoSearch()
    if not api_key:
        raise ValueError(f"search provider {provider!r} needs MERIDIAN_SEARCH_API_KEY")
    return {"tavily": TavilySearch, "brave": BraveSearch, "serper": SerperSearch}[provider](api_key)
