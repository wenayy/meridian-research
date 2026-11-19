"""Fetch a URL and extract the readable main text (boilerplate stripped), with caching.

Extraction heuristic: drop script/style/nav/footer/aside/form, prefer <article> or <main>,
otherwise keep paragraphs and list items with enough words. Good enough for news,
blogs and docs; PDFs are handled with pypdf when available.
"""
from __future__ import annotations

import io
import re

from ..tracing import span
from .cache import Cache
from .search import LocalCorpus

MAX_CHARS = 12_000
_NOISE = ["script", "style", "noscript", "nav", "footer", "header", "aside", "form", "svg", "iframe"]


def extract_text(html: str) -> tuple[str, str]:
    """Return (title, main_text) from an HTML page."""
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    title = (soup.title.get_text(strip=True) if soup.title else "")[:200]
    for tag in soup(_NOISE):
        tag.decompose()
    root = soup.find("article") or soup.find("main") or soup.body or soup
    blocks = []
    for el in root.find_all(["h1", "h2", "h3", "p", "li", "td"]):
        t = re.sub(r"\s+", " ", el.get_text(" ", strip=True))
        if el.name.startswith("h") or len(t.split()) >= 6:
            blocks.append(t)
    text = "\n".join(dict.fromkeys(blocks))  # drop repeated blocks, keep order
    return title, text[:MAX_CHARS]


class Fetcher:
    def __init__(self, cache: Cache, corpus: LocalCorpus | None = None):
        self.cache, self.corpus = cache, corpus

    def fetch(self, url: str) -> dict:
        """Returns {url, title, text, ok, error?}. Never raises."""
        with span("tool.fetch", url=url) as s:
            if self.corpus is not None:
                p = self.corpus.page(url)
                s.set(local=True, ok=bool(p))
                return {"url": url, "title": p["title"], "text": p["text"][:MAX_CHARS], "ok": True} if p else \
                    {"url": url, "title": "", "text": "", "ok": False, "error": "not in local corpus"}
            key = Cache.key("fetch", url)
            hit = self.cache.get(key)
            if hit is not None:
                s.set(cached=True)
                return hit
            try:
                import httpx

                with httpx.Client(timeout=20, follow_redirects=True,
                                  headers={"User-Agent": "Mozilla/5.0 (Meridian research agent)"}) as c:
                    r = c.get(url)
                    r.raise_for_status()
                ctype = r.headers.get("content-type", "")
                if "pdf" in ctype or url.lower().endswith(".pdf"):
                    from pypdf import PdfReader

                    text = "\n".join(p.extract_text() or "" for p in PdfReader(io.BytesIO(r.content)).pages)
                    out = {"url": url, "title": url.rsplit("/", 1)[-1], "text": text[:MAX_CHARS], "ok": True}
                else:
                    title, text = extract_text(r.text)
                    out = {"url": url, "title": title, "text": text, "ok": bool(text)}
            except Exception as e:  # network errors are data, not crashes
                out = {"url": url, "title": "", "text": "", "ok": False, "error": f"{type(e).__name__}: {e}"}
            s.set(ok=out["ok"], chars=len(out["text"]))
            if out["ok"]:
                self.cache.set(key, out)
            return out
