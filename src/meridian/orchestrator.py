"""The research orchestrator: a planner/worker/editor multi-agent pipeline.

   question
      │  guardrail (regex tripwire + LLM classifier)
      ▼
   PLANNER ── sub-questions + search queries
      │
      ▼   round 1..N  (fan-out, bounded thread pool)
   RESEARCHERS ── search → dedupe URLs → credibility score → fetch → near-dup filter
      │            → extract claims with verbatim quotes → verify quotes against page text
      ▼
   REVIEWER ── coverage check, new gap sub-questions, cross-source CONFLICTS
      │  gaps & budget left? ──► another research round on the gaps only
      ▼
   WRITERS ── outline → sections written in parallel (cite [n]) → executive summary
      │
      ▼
   CITATION AUDITOR ── drop unsupported sentences and citations to non-existent sources
      ▼
   Report (markdown/HTML) + stats + full trace

Budgets (LLM calls, USD, wall-clock) are checked between phases; when one is hit the agent stops
researching and writes up what it has, instead of failing.
"""
from __future__ import annotations

import contextvars
import re
import threading
import time
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import TypeVar

from .agents import roles
from .config import Settings
from .credibility import domain_of, is_near_duplicate, score_source, shingles
from .llm import LLM
from .models import Evidence, Report, ResearchPlan, Source, SubQuestion
from .tools.fetch import Fetcher
from .tools.search import SearchProvider
from .tracing import current_trace, span, trace

T = TypeVar("T")
R = TypeVar("R")
EventFn = Callable[[str, dict], None]


class Blocked(Exception):
    """Raised when the input guardrail refuses a request."""


@dataclass
class Budget:
    max_llm_calls: int = 120
    max_cost_usd: float = 1.0
    max_seconds: float = 600
    started: float = field(default_factory=time.perf_counter)

    def exceeded(self) -> str | None:
        t = current_trace()
        if t and t.usage.llm_calls >= self.max_llm_calls:
            return f"LLM call budget ({self.max_llm_calls}) reached"
        if t and t.usage.cost_usd >= self.max_cost_usd:
            return f"cost budget (${self.max_cost_usd:.2f}) reached"
        if time.perf_counter() - self.started >= self.max_seconds:
            return f"time budget ({self.max_seconds:.0f}s) reached"
        return None


def pmap(fn: Callable[[T], R], items: Iterable[T], workers: int) -> list[R]:
    """Parallel map that preserves order AND the caller's contextvars (so tracing spans nest)."""
    items = list(items)
    if workers <= 1 or len(items) <= 1:
        return [fn(x) for x in items]
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = [ex.submit(contextvars.copy_context().run, fn, x) for x in items]
        return [f.result() for f in futs]


class ResearchRun:
    """Mutable state of one research job (sources and evidence registries are thread-safe)."""

    def __init__(self, question: str):
        self.question = question
        self.sources: dict[int, Source] = {}
        self.by_url: dict[str, int] = {}
        self.evidence: list[Evidence] = []
        self.fingerprints: list[set[int]] = []
        self.fetched: set[int] = set()
        self.extracted: set[tuple[int, str]] = set()
        self.duplicates: set[int] = set()
        self.lock = threading.Lock()
        self.stats = {"searches": 0, "pages_fetched": 0, "fetch_failures": 0, "near_duplicates": 0,
                      "claims": 0, "claims_quote_verified": 0, "rounds": 0, "unsupported_removed": 0,
                      "low_credibility_skipped": 0}

    def register(self, url: str, title: str, snippet: str, published: str | None, sq_id: str) -> tuple[Source, bool]:
        with self.lock:
            if url in self.by_url:
                src = self.sources[self.by_url[url]]
                if sq_id not in src.found_for:
                    src.found_for.append(sq_id)
                return src, False
            cred, reasons = score_source(url, title, published)
            src = Source(id=len(self.sources) + 1, url=url, title=title, domain=domain_of(url), published=published,
                         snippet=snippet, credibility=cred, credibility_reasons=reasons, found_for=[sq_id])
            self.sources[src.id] = src
            self.by_url[url] = src.id
            return src, True

    def add_evidence(self, ev: list[Evidence]) -> None:
        with self.lock:
            base = len(self.evidence) + 1
            for i, e in enumerate(ev):
                e.id = base + i
            self.evidence.extend(ev)
            self.stats["claims"] += len(ev)
            self.stats["claims_quote_verified"] += sum(e.quote_verified for e in ev)


class Researcher:
    def __init__(self, settings: Settings, llm: LLM, search: SearchProvider, fetcher: Fetcher,
                 fast_llm: LLM | None = None, on_event: EventFn | None = None):
        self.s, self.llm, self.search, self.fetcher = settings, llm, search, fetcher
        self.fast_llm = fast_llm or llm
        self.emit = on_event or (lambda _t, _d: None)

    # ------------------------------------------------------------------ one sub-question
    def research_sub_question(self, run: ResearchRun, sq: SubQuestion) -> int:
        with span("research.sub_question", id=sq.id, question=sq.question) as s:
            candidates: dict[int, Source] = {}
            for q in sq.search_queries:
                try:
                    results = self.search.search(q, self.s.results_per_query)
                except Exception as e:
                    self.emit("warning", {"message": f"search failed for '{q}': {e}"})
                    continue
                with run.lock:
                    run.stats["searches"] += 1
                for r in results:
                    src, _ = run.register(r.url, r.title, r.snippet, r.published, sq.id)
                    candidates[src.id] = src  # pages already read for another sub-question are reused, not refetched
            # read the most credible pages first; skip junk unless there is nothing else
            ranked = sorted(candidates.values(), key=lambda x: -x.credibility)
            good = [c for c in ranked if c.credibility >= self.s.min_credibility]
            picked = (good if len(good) >= 2 else ranked)[: self.s.sources_per_sub_question]
            with run.lock:
                run.stats["low_credibility_skipped"] += len(ranked) - len(good) if len(good) >= 2 else 0
            self.emit("sources", {"sub_question": sq.id, "sources": [
                {"id": c.id, "title": c.title, "domain": c.domain, "credibility": c.credibility} for c in picked]})

            def read(src: Source) -> list[Evidence]:
                with run.lock:
                    if (src.id, sq.id) in run.extracted:
                        return []
                    run.extracted.add((src.id, sq.id))
                    first_read = src.id not in run.fetched
                    run.fetched.add(src.id)
                if first_read:
                    page = self.fetcher.fetch(src.url)
                    with run.lock:
                        run.stats["pages_fetched"] += 1
                        if not page["ok"]:
                            run.stats["fetch_failures"] += 1
                    src.text = page["text"] if page["ok"] else src.snippet  # fall back to the search snippet
                    src.title = src.title or page["title"]
                    fp = shingles(src.text)
                    with run.lock:
                        dup = len(src.text) > 400 and is_near_duplicate(src.text, run.fingerprints)
                        if dup:
                            run.stats["near_duplicates"] += 1
                            run.duplicates.add(src.id)
                            src.credibility_reasons.append("near-duplicate of another source (syndicated)")
                        else:
                            run.fingerprints.append(fp)
                if src.id in run.duplicates or not src.text:
                    return []
                return roles.extract(self.fast_llm, sq, src, 0)

            ev = [e for chunk in pmap(read, picked, min(4, self.s.max_workers)) for e in chunk]
            run.add_evidence(ev)
            s.set(sources=len(picked), claims=len(ev))
            self.emit("evidence", {"sub_question": sq.id, "claims": len(ev)})
            return len(ev)


class Orchestrator:
    def __init__(self, settings: Settings, llm: LLM, search: SearchProvider, fetcher: Fetcher,
                 fast_llm: LLM | None = None):
        self.s, self.llm, self.search, self.fetcher = settings, llm, search, fetcher
        self.fast_llm = fast_llm or llm

    def run(self, question: str, on_event: EventFn | None = None) -> Report:
        emit = on_event or (lambda _t, _d: None)
        s = self.s
        budget = Budget(s.max_llm_calls, s.max_cost_usd, s.max_seconds)
        with trace("research.run") as t:
            t.root.set(question=question)
            verdict = roles.check_input(self.llm, question)
            if not verdict.allowed:
                emit("blocked", {"reason": verdict.reason})
                raise Blocked(verdict.reason)

            plan: ResearchPlan = roles.plan(self.llm, question, s.min_sub_questions, s.max_sub_questions)
            emit("plan", {"objective": plan.objective,
                          "sub_questions": [q.model_dump() for q in plan.sub_questions]})

            run = ResearchRun(question)
            researcher = Researcher(s, self.llm, self.search, self.fetcher, self.fast_llm, emit)
            pending = list(plan.sub_questions)
            conflicts = []
            stop_reason = None
            for rnd in range(1, s.max_rounds + 1):
                run.stats["rounds"] = rnd
                emit("round", {"round": rnd, "sub_questions": [q.id for q in pending]})
                with span("research.round", round=rnd):
                    pmap(lambda q: researcher.research_sub_question(run, q), pending, s.max_workers)
                if (stop_reason := budget.exceeded()):
                    emit("budget", {"reason": stop_reason})
                    break
                rev = roles.review(self.llm, question, plan, run.evidence, run.sources, s.max_gaps_per_round)
                conflicts = rev.conflicts or conflicts
                emit("review", {"round": rnd, "covered": rev.covered, "gaps": [g.question for g in rev.gaps],
                                "conflicts": [c.model_dump() for c in rev.conflicts]})
                if not rev.gaps or rnd == s.max_rounds:
                    break
                plan.sub_questions.extend(rev.gaps)
                pending = rev.gaps

            # ---------------- write
            if not run.evidence:
                emit("warning", {"message": "no evidence collected; report will be thin"})
            outline = roles.outline(self.llm, question, plan, run.evidence, run.sources)
            emit("outline", {"title": outline.title, "sections": [x.heading for x in outline.sections]})
            drafts = pmap(lambda sec: roles.write_section(self.llm, question, sec, run.evidence, run.sources),
                          outline.sections, s.max_workers)
            emit("drafted", {"sections": len(drafts)})

            # ---------------- audit citations
            audited = pmap(lambda d: roles.verify_section(self.llm, d, run.evidence, run.sources), drafts, s.max_workers)
            sections = [d for d, _ in audited]
            removed = [x for _, rm in audited for x in rm]
            run.stats["unsupported_removed"] = len(removed)
            emit("verified", {"removed": removed})

            summary = roles.summarize(self.llm, outline.title, sections)
            report = finalize(question, outline.title, summary.executive_summary, summary.key_takeaways, sections,
                              conflicts, run)
            report.stats.update(run.stats)
            report.stats.update({
                "trace_id": t.id, "sub_questions": len(plan.sub_questions),
                "sources_considered": len(run.sources), "sources_cited": len(report.sources),
                "budget_stop": stop_reason,
                "llm_calls": t.usage.llm_calls, "tokens": t.usage.prompt_tokens + t.usage.completion_tokens,
                "cost_usd": round(t.usage.cost_usd, 4),
            })
            t.root.set(**{k: v for k, v in report.stats.items() if isinstance(v, int | float | str | type(None))})
        report.stats["seconds"] = round(t.root.duration_ms / 1000, 2)
        emit("done", {"stats": report.stats})
        return report


def finalize(question, title, summary, takeaways, sections, conflicts, run: ResearchRun) -> Report:
    """Renumber citations 1..n in order of first appearance and keep only cited sources."""
    order: dict[int, int] = {}
    texts = [summary, *takeaways, *(s.markdown for s in sections)]
    for txt in texts:
        for n in re.findall(r"\[(\d+)\]", txt):
            sid = int(n)
            if sid in run.sources and sid not in order:
                order[sid] = len(order) + 1

    def renum(txt: str) -> str:
        return re.sub(r"\[(\d+)\]", lambda m: f"[{order[int(m.group(1))]}]" if int(m.group(1)) in order else "", txt)

    new_sections = [s.model_copy(update={"markdown": renum(s.markdown)}) for s in sections]
    sources = []
    for sid, n in sorted(order.items(), key=lambda x: x[1]):
        src = run.sources[sid].model_copy(update={"id": n, "text": ""})
        sources.append(src)
    ev_to_src = {e.id: e.source_id for e in run.evidence}
    fixed_conflicts = []
    for c in conflicts:
        ids = [order[ev_to_src[i]] for i in c.evidence_ids if i in ev_to_src and ev_to_src[i] in order]
        fixed_conflicts.append(c.model_copy(update={"evidence_ids": ids}))
    return Report(question=question, title=title, executive_summary=renum(summary),
                  key_takeaways=[renum(x) for x in takeaways], sections=new_sections, conflicts=fixed_conflicts,
                  sources=sources)


def build_orchestrator(settings: Settings | None = None, llm: LLM | None = None, fast_llm: LLM | None = None):
    from .config import get_settings
    from .llm import OpenAICompatLLM
    from .tools.cache import Cache
    from .tools.search import CachedSearch, LocalCorpus, make_search

    s = settings or get_settings()
    cache = Cache(s.cache_path)
    provider = make_search(s.search_provider, s.search_api_key, s.local_corpus)
    fetcher = Fetcher(cache, provider if isinstance(provider, LocalCorpus) else None)
    llm = llm or OpenAICompatLLM(s.llm_model, s.llm_base_url, s.llm_api_key)
    if fast_llm is None and s.fast_model:
        fast_llm = OpenAICompatLLM(s.fast_model, s.llm_base_url, s.llm_api_key)
    return Orchestrator(s, llm, CachedSearch(provider, cache), fetcher, fast_llm)
