"""The agent roles. Each is a small function around one structured LLM call, so it can be tested
in isolation and swapped for a different model (e.g. a cheap model for extraction, a strong one
for writing)."""
from __future__ import annotations

import re
from datetime import date

from .. import prompts
from ..llm import LLM, structured
from ..models import (
    CitationCheck,
    Evidence,
    Extraction,
    GuardrailVerdict,
    Outline,
    OutlineSection,
    ResearchPlan,
    Review,
    SectionDraft,
    Source,
    SubQuestion,
    Summary,
)
from ..tracing import span

# ---------------------------------------------------------------- guardrail

_HARD_BLOCK = re.compile(r"\b(make|build|synthesi[sz]e)\b.{0,40}\b(bomb|explosive|nerve agent|ransomware|malware)\b", re.I)


def check_input(llm: LLM, question: str) -> GuardrailVerdict:
    """Two layers: a cheap regex tripwire (no LLM cost) then an LLM classifier for nuance."""
    with span("agent.guardrail"):
        if _HARD_BLOCK.search(question):
            return GuardrailVerdict(allowed=False, reason="matched hard-block pattern")
        try:
            return structured(llm, [{"role": "system", "content": prompts.GUARD},
                                    {"role": "user", "content": question}], GuardrailVerdict)
        except Exception:
            return GuardrailVerdict(allowed=True, reason="classifier unavailable; regex passed")


# ---------------------------------------------------------------- planner


def plan(llm: LLM, question: str, n_min: int = 3, n_max: int = 5) -> ResearchPlan:
    with span("agent.plan") as s:
        p = structured(llm, [
            {"role": "system", "content": prompts.PLAN.format(n_min=n_min, n_max=n_max, today=date.today().isoformat())},
            {"role": "user", "content": f"RESEARCH QUESTION: {question}"}], ResearchPlan)
        p.sub_questions = p.sub_questions[:n_max]
        for i, q in enumerate(p.sub_questions, 1):  # normalise ids so later stages can rely on them
            q.id = f"q{i}"
        s.set(sub_questions=[q.question for q in p.sub_questions])
        return p


# ---------------------------------------------------------------- extraction


def _norm(t: str) -> str:
    return re.sub(r"[^a-z0-9%.$ ]", "", re.sub(r"\s+", " ", t.lower())).strip()


def quote_in_text(quote: str, text: str) -> bool:
    """Is the 'verbatim' quote really in the page? Catches fabricated quotes cheaply."""
    q = _norm(quote)
    return len(q) >= 12 and q in _norm(text)


def extract(llm: LLM, sub_q: SubQuestion, source: Source, start_id: int) -> list[Evidence]:
    with span("agent.extract", source=source.id, sub_question=sub_q.id) as s:
        try:
            out = structured(llm, [
                {"role": "system", "content": prompts.EXTRACT},
                {"role": "user", "content": f"SUB-QUESTION: {sub_q.question}\nPAGE TITLE: {source.title}\n"
                                            f"URL: {source.url}\n\nSOURCE TEXT:\n{source.text[:9000]}"}], Extraction)
        except Exception:
            return []
        if not out.relevant:
            s.set(relevant=False)
            return []
        ev = [Evidence(id=start_id + i, source_id=source.id, sub_question_id=sub_q.id, claim=c.claim, quote=c.quote,
                       quote_verified=quote_in_text(c.quote, source.text)) for i, c in enumerate(out.claims)]
        s.set(claims=len(ev), verified=sum(e.quote_verified for e in ev))
        return ev


# ---------------------------------------------------------------- review


def format_evidence(evidence: list[Evidence], sources: dict[int, Source], with_ids: bool = True) -> str:
    lines = []
    for e in evidence:
        src = sources[e.source_id]
        tag = f"E{e.id} " if with_ids else ""
        flag = "" if e.quote_verified else " (quote unverified)"
        lines.append(f"- {tag}[S{src.id}, credibility {src.credibility:.2f}, {src.domain}] ({e.sub_question_id}) "
                     f"{e.claim}{flag}")
    return "\n".join(lines)


def review(llm: LLM, question: str, plan_: ResearchPlan, evidence: list[Evidence], sources: dict[int, Source],
           max_gaps: int = 3) -> Review:
    with span("agent.review", evidence=len(evidence)) as s:
        subqs = "\n".join(f"- {q.id}: {q.question}" for q in plan_.sub_questions)
        try:
            r = structured(llm, [
                {"role": "system", "content": prompts.REVIEW.format(max_gaps=max_gaps)},
                {"role": "user", "content": f"MAIN QUESTION: {question}\n\nSUB-QUESTIONS:\n{subqs}\n\n"
                                            f"EVIDENCE:\n{format_evidence(evidence, sources)}"}], Review)
        except Exception:
            r = Review(covered=[q.id for q in plan_.sub_questions])
        r.gaps = r.gaps[:max_gaps]
        for i, g in enumerate(r.gaps, 1):
            g.id = f"g{len(plan_.sub_questions) + i}"
        s.set(covered=r.covered, gaps=[g.question for g in r.gaps], conflicts=len(r.conflicts))
        return r


# ---------------------------------------------------------------- writing


def outline(llm: LLM, question: str, plan_: ResearchPlan, evidence: list[Evidence], sources: dict[int, Source]) -> Outline:
    with span("agent.outline"):
        subqs = "\n".join(f"- {q.id}: {q.question}" for q in plan_.sub_questions)
        try:
            return structured(llm, [
                {"role": "system", "content": prompts.OUTLINE},
                {"role": "user", "content": f"QUESTION: {question}\n\nSUB-QUESTIONS:\n{subqs}\n\n"
                                            f"EVIDENCE:\n{format_evidence(evidence, sources, with_ids=False)}"}], Outline)
        except Exception:  # fall back to one section per sub-question
            return Outline(title=question, sections=[OutlineSection(heading=q.question, goal=q.question,
                                                                    sub_question_ids=[q.id])
                                                     for q in plan_.sub_questions])


def write_section(llm: LLM, question: str, sec: OutlineSection, evidence: list[Evidence],
                  sources: dict[int, Source]) -> SectionDraft:
    with span("agent.write_section", heading=sec.heading) as s:
        relevant = [e for e in evidence if e.sub_question_id in sec.sub_question_ids] or evidence
        relevant = sorted(relevant, key=lambda e: -sources[e.source_id].credibility)[:40]
        msg = (f"REPORT QUESTION: {question}\nSECTION: {sec.heading}\nGOAL: {sec.goal}\n\n"
               f"EVIDENCE:\n{format_evidence(relevant, sources, with_ids=False)}")
        md = llm.complete([{"role": "system", "content": prompts.SECTION}, {"role": "user", "content": msg}])
        md = re.sub(r"^#\s.*\n", "", md.strip())  # writers sometimes add a heading anyway
        s.set(chars=len(md), citations=len(re.findall(r"\[\d+\]", md)))
        return SectionDraft(heading=sec.heading, markdown=md)


def summarize(llm: LLM, title: str, sections: list[SectionDraft]) -> Summary:
    with span("agent.summary"):
        body = "\n\n".join(f"## {s.heading}\n{s.markdown}" for s in sections)
        try:
            return structured(llm, [{"role": "system", "content": prompts.SUMMARY},
                                    {"role": "user", "content": f"# {title}\n\n{body}"}], Summary)
        except Exception:
            first = re.split(r"(?<=[.!?])\s", sections[0].markdown)[0] if sections else ""
            return Summary(executive_summary=first, key_takeaways=[s.heading for s in sections][:5] or ["-"])


# ---------------------------------------------------------------- citation verification

_SENT = re.compile(r"(?<=[.!?])\s+(?=[A-Z\-*|])")


def verify_section(llm: LLM, draft: SectionDraft, evidence: list[Evidence], sources: dict[int, Source]) -> tuple[SectionDraft, list[str]]:
    """Audit every cited sentence against the evidence of the sources it cites; drop unsupported ones.

    Also drops citations to source numbers that don't exist (a classic hallucination)."""
    with span("agent.verify_section", heading=draft.heading) as s:
        cited = {int(n) for n in re.findall(r"\[(\d+)\]", draft.markdown)}
        bogus = cited - set(sources)
        md = draft.markdown
        for n in bogus:
            md = md.replace(f"[{n}]", "")
        ev = [e for e in evidence if e.source_id in cited]
        if not ev:
            s.set(unsupported=0, bogus_citations=len(bogus))
            return SectionDraft(heading=draft.heading, markdown=md), []
        by_src = "\n".join(f"[{e.source_id}] {e.claim} | quote: {e.quote}" for e in ev)
        try:
            chk = structured(llm, [{"role": "system", "content": prompts.VERIFY},
                                   {"role": "user", "content": f"EVIDENCE BY SOURCE:\n{by_src}\n\nDRAFT:\n{md}"}],
                             CitationCheck)
        except Exception:
            return SectionDraft(heading=draft.heading, markdown=md), []
        removed = []
        for sent in chk.unsupported:
            sent = sent.strip()
            if sent and sent in md:
                md = md.replace(sent, "").replace("  ", " ")
                removed.append(sent)
        md = re.sub(r"\n{3,}", "\n\n", md).strip()
        s.set(unsupported=len(removed), bogus_citations=len(bogus))
        return SectionDraft(heading=draft.heading, markdown=md), removed
