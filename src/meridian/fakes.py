"""A deterministic, rule-based model that plays every agent role.

Used by the test-suite and by ``meridian demo`` so the full multi-agent pipeline (planning, parallel
research, extraction, review, writing, citation audit) can run offline and in CI. It is
intentionally simple: it extracts sentences that share words with the sub-question.
"""
from __future__ import annotations

import json
import re

_STOP = set("the a an of and or in on to for is are was were by with what which who how does do at as from its "
            "this that be will has have about into than".split())


def _words(t: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", t.lower()) if w not in _STOP and len(w) > 2}


def _field(text: str, label: str) -> str:
    m = re.search(rf"{label}:\s*(.+)", text)
    return m.group(1).strip() if m else ""


def scripted_handler(messages: list[dict], json_mode: bool) -> str:
    system, user = messages[0]["content"], messages[-1]["content"]
    if "TASK:GUARD" in system:
        bad = re.search(r"\b(bomb|explosive|malware|home address of)\b", user, re.I)
        return json.dumps({"allowed": not bad, "reason": "unsafe request" if bad else "ok"})
    if "TASK:PLAN" in system:
        q = _field(user, "RESEARCH QUESTION")
        core = " ".join(sorted(_words(q), key=q.lower().find)[:6])
        subs = [{"id": "q1", "question": f"What is the current state of {core}?", "search_queries": [q, f"{core} 2025"]},
                {"id": "q2", "question": f"Who are the key players in {core}?", "search_queries": [f"{core} companies"]},
                {"id": "q3", "question": f"What are the costs and risks of {core}?", "search_queries": [f"{core} cost risks"]}]
        return json.dumps({"objective": q, "sub_questions": subs})
    if "TASK:EXTRACT" in system:
        sq = _words(_field(user, "SUB-QUESTION"))
        text = user.split("SOURCE TEXT:", 1)[-1]
        sents = [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if len(s.split()) >= 6]
        scored = sorted(sents, key=lambda s: -len(_words(s) & sq))
        claims = [{"claim": s, "quote": s} for s in scored[:3] if _words(s) & sq]
        return json.dumps({"relevant": bool(claims), "claims": claims})
    if "TASK:REVIEW" in system:
        ids = re.findall(r"^- (q\d+):", user, re.M)
        return json.dumps({"covered": ids, "gaps": [], "conflicts": []})
    if "TASK:OUTLINE" in system:
        subs = re.findall(r"^- (q\d+|g\d+): (.+)$", user, re.M)
        q = _field(user, "QUESTION")
        return json.dumps({"title": f"Research report: {q}",
                           "sections": [{"heading": t.rstrip("?"), "goal": t, "sub_question_ids": [i]} for i, t in subs]})
    if "TASK:SECTION" in system:
        lines = re.findall(r"^- \[S(\d+), [^\]]*\] \([^)]*\) (.+?)(?: \(quote unverified\))?$", user, re.M)
        seen, out = set(), []
        for sid, claim in lines:
            if claim not in seen:
                seen.add(claim)
                out.append(f"{claim.rstrip('.')} [{sid}].")
            if len(out) >= 4:
                break
        return " ".join(out) or "No evidence was found for this section."
    if "TASK:SUMMARY" in system:
        first = re.search(r"^[^#\n].+?\[\d+\]\.", user, re.M)
        heads = re.findall(r"^## (.+)$", user, re.M)
        return json.dumps({"executive_summary": first.group(0) if first else "Summary unavailable.",
                           "key_takeaways": heads[:4] or ["See sections below."]})
    if "TASK:VERIFY" in system:
        return json.dumps({"unsupported": []})
    return "ok"
