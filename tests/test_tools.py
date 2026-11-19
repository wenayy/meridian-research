import contextvars
import threading
from datetime import date

from meridian.agents.roles import quote_in_text
from meridian.credibility import is_near_duplicate, score_source, shingles
from meridian.orchestrator import pmap
from meridian.tools.cache import Cache
from meridian.tools.fetch import extract_text
from meridian.tools.search import CachedSearch, LocalCorpus
from meridian.tracing import span, trace

TODAY = date(2026, 1, 1)


def test_credibility_ordering_and_reasons():
    gov, _ = score_source("https://www.energy-board.gov/r", published="2025-05-01", today=TODAY)
    news, _ = score_source("https://www.reuters.com/x", published="2025-05-01", today=TODAY)
    press, why_press = score_source("https://acme.com/press/launch", published="2025-05-01", today=TODAY)
    farm, why_farm = score_source("https://top-10-stocks.blog/x", title="Top 10 stocks", today=TODAY)
    old, why_old = score_source("https://www.reuters.com/y", published="2019-01-01", today=TODAY)
    assert gov > news > press > farm
    assert old < news and any("years ago" in r for r in why_old)
    assert any("press release" in r for r in why_press)
    assert any("content-farm" in r for r in why_farm)


def test_near_duplicate_detection():
    a = "Saltwave Cells opened a sodium-ion cell factory in Pune with an annual capacity of 12 GWh in its first phase."
    b = a + " Analysts welcomed the move."
    c = "Lithium carbonate prices have fallen sharply since the 2022 peak, testing sodium-ion economics."
    assert is_near_duplicate(b, [shingles(a)], threshold=0.6)
    assert not is_near_duplicate(c, [shingles(a)])


def test_extract_text_strips_boilerplate():
    html = """<html><head><title>T</title><script>var x=1</script></head><body>
      <nav>Home About Contact Login Signup Pricing</nav>
      <article><h1>Heading</h1><p>This paragraph has more than six words in it.</p><p>short</p></article>
      <footer>Copyright footer text with many many words here</footer></body></html>"""
    title, text = extract_text(html)
    assert title == "T"
    assert "This paragraph has more than six words" in text and "Heading" in text
    assert "short" not in text and "Copyright" not in text and "var x" not in text


def test_quote_verification_catches_fabrication():
    page = "Average sodium-ion cell prices fell to USD 58 per kWh in 2025, compared with USD 72."
    assert quote_in_text("average sodium-ion cell prices fell to USD 58 per kWh", page)
    assert not quote_in_text("cell prices fell to USD 45 per kWh", page)
    assert not quote_in_text("USD 58", page)  # too short to count as a quote


def test_cache_and_cached_search(tmp_path, settings):
    corpus = LocalCorpus(settings.local_corpus)
    cache = Cache(tmp_path / "c.sqlite")
    cs = CachedSearch(corpus, cache)
    first = cs.search("Saltwave Pune plant capacity", 3)
    second = cs.search("Saltwave Pune plant capacity", 3)
    assert [r.url for r in first] == [r.url for r in second]
    assert cache.hits == 1 and cache.misses == 1
    assert any("saltwave" in r.url or "Saltwave" in r.title for r in first)


def test_local_corpus_ranks_relevant_page_first(settings):
    top = LocalCorpus(settings.local_corpus).search("hard carbon anode supply outlook 2030 stationary storage", 3)
    assert "iea.org" in top[0].url


def test_pmap_preserves_order_and_trace_context():
    seen_threads = set()

    def work(i):
        seen_threads.add(threading.get_ident())
        with span(f"work{i}"):
            return i * i

    with trace("t", store=None) as t:
        out = pmap(work, range(6), workers=3)
    assert out == [0, 1, 4, 9, 16, 25]
    assert sorted(c.name for c in t.root.children) == sorted(f"work{i}" for i in range(6))
    assert len(seen_threads) > 1
    assert contextvars.copy_context() is not None
