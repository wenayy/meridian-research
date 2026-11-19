"""Benchmark harness for research quality on the offline synthetic web.

Metrics per question:
  * fact_recall        share of required facts that appear in the report
  * trap_rate          share of trap facts (content-farm / stale / marketing claims) stated in the
                       report body *as fact* (mentions inside the "Where sources disagree" section
                       don't count; surfacing a disagreement is the right behaviour)
  * cited_credibility  mean credibility of cited sources
  * low_cred_cited     number of cited sources with credibility < 0.4
  * conflicts_found    disagreements surfaced by the reviewer
  * citation stats, unsupported sentences removed, LLM calls, tokens, cost, seconds
"""
from __future__ import annotations

import json
import re
import statistics
from pathlib import Path

from ..models import Report
from ..orchestrator import Orchestrator


def _norm(t: str) -> str:
    return t.lower().replace(",", "")


def contains(text: str, fact: str) -> bool:
    """Boundary-aware match, so "6 billion" does not match inside "0.6 billion"."""
    return re.search(rf"(?<![\w.]){re.escape(_norm(fact))}(?![\w])", _norm(text)) is not None


def score(report: Report, required: list[str], traps: list[str]) -> dict:
    md = report.markdown()
    body = md.split("## Where sources disagree")[0] if "## Where sources disagree" in md else md.split("## Sources")[0]
    recall = sum(contains(md, f) for f in required) / len(required) if required else 1.0
    trap = sum(contains(body, f) for f in traps) / len(traps) if traps else 0.0
    creds = [s.credibility for s in report.sources]
    return {
        "fact_recall": round(recall, 3),
        "trap_rate": round(trap, 3),
        "cited_sources": len(report.sources),
        "cited_credibility": round(statistics.fmean(creds), 3) if creds else 0.0,
        "low_cred_cited": sum(c < 0.4 for c in creds),
        "conflicts_found": len(report.conflicts),
        "unsupported_removed": report.stats.get("unsupported_removed", 0),
        "llm_calls": report.stats.get("llm_calls", 0),
        "tokens": report.stats.get("tokens", 0),
        "cost_usd": report.stats.get("cost_usd", 0.0),
        "seconds": report.stats.get("seconds", 0.0),
    }


def run_benchmark(orch: Orchestrator, questions_path: Path, out_dir: Path | None = None) -> dict:
    qs = [json.loads(line) for line in Path(questions_path).open() if line.strip()]
    rows = []
    for q in qs:
        rep = orch.run(q["question"])
        row = {"id": q["id"], **score(rep, q["required_facts"], q["trap_facts"])}
        rows.append(row)
        if out_dir:
            out_dir.mkdir(parents=True, exist_ok=True)
            (out_dir / f"{q['id']}.md").write_text(rep.markdown())
    keys = [k for k in rows[0] if k != "id"]
    summary = {k: round(statistics.fmean(r[k] for r in rows), 3) for k in keys}
    return {"summary": summary, "per_question": rows}


def markdown_table(res: dict) -> str:
    cols = ["fact_recall", "trap_rate", "cited_sources", "cited_credibility", "low_cred_cited", "conflicts_found",
            "unsupported_removed", "llm_calls", "cost_usd", "seconds"]
    head = "| question | " + " | ".join(cols) + " |\n|" + "---|" * (len(cols) + 1)
    rows = ["| " + r["id"] + " | " + " | ".join(str(r[c]) for c in cols) + " |" for r in res["per_question"]]
    rows.append("| **mean** | " + " | ".join(f"**{res['summary'][c]}**" for c in cols) + " |")
    return "\n".join([head, *rows])
