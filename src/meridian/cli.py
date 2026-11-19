"""Command line: ``meridian research | bench | serve``."""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from .config import get_settings


def _printer(kind: str, data: dict) -> None:
    msg = {
        "plan": lambda d: "plan: " + " | ".join(q["question"] for q in d["sub_questions"]),
        "round": lambda d: f"round {d['round']}: researching {', '.join(d['sub_questions'])}",
        "sources": lambda d: f"  {d['sub_question']}: reading " + ", ".join(
            f"{s['domain']} ({s['credibility']:.2f})" for s in d["sources"]),
        "evidence": lambda d: f"  {d['sub_question']}: {d['claims']} claims",
        "review": lambda d: f"review: covered {d['covered']}; gaps {d['gaps']}; conflicts {len(d['conflicts'])}",
        "outline": lambda d: "outline: " + " · ".join(d["sections"]),
        "verified": lambda d: f"citation audit: removed {len(d['removed'])} unsupported sentence(s)",
        "budget": lambda d: f"budget stop: {d['reason']}",
        "warning": lambda d: f"warning: {d['message']}",
        "done": lambda d: "done: {llm_calls} LLM calls, {tokens} tokens, ${cost_usd}, {seconds}s".format(**d["stats"]),
    }.get(kind)
    if msg:
        print(msg(data), file=sys.stderr)


def cmd_research(args) -> None:
    from .orchestrator import Blocked, build_orchestrator
    from .report import render_html

    s = get_settings()
    if args.depth:
        s.max_rounds = args.depth
    if args.offline:
        from .llm import FakeLLM

        s.search_provider = "local"
        orch = build_orchestrator(s, llm=FakeLLM())
    else:
        orch = build_orchestrator(s)
    try:
        rep = orch.run(args.question, on_event=_printer if not args.quiet else None)
    except Blocked as e:
        sys.exit(f"blocked by guardrail: {e}")
    out = Path(args.out) if args.out else s.reports_dir / (re.sub(r"[^a-z0-9]+", "-", args.question.lower())[:60].strip("-") + ".md")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(rep.markdown())
    out.with_suffix(".html").write_text(render_html(rep))
    print(f"\nreport written to {out} (+ .html)", file=sys.stderr)
    if args.print:
        print(rep.markdown())


def cmd_bench(args) -> None:
    from .eval.bench import markdown_table, run_benchmark
    from .orchestrator import build_orchestrator

    s = get_settings()
    s.search_provider = "local"
    llm = None
    if args.offline:
        from .llm import FakeLLM

        llm = FakeLLM()
    res = run_benchmark(build_orchestrator(s, llm=llm), Path(args.questions), Path(args.out))
    table = markdown_table(res)
    (Path(args.out) / "results.md").write_text(table + "\n")
    (Path(args.out) / "results.json").write_text(json.dumps(res, indent=2))
    print(table)


def cmd_serve(args) -> None:
    import uvicorn

    uvicorn.run("meridian.api.server:create_app", factory=True, host=args.host, port=args.port)


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="meridian", description="Meridian: autonomous deep-research agent")
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("research", help="research a question and write a cited report")
    r.add_argument("question")
    r.add_argument("-o", "--out", help="output .md path (an .html is written alongside)")
    r.add_argument("--depth", type=int, help="max research rounds (1 = no gap filling)")
    r.add_argument("--print", action="store_true", help="also print the report to stdout")
    r.add_argument("--quiet", action="store_true")
    r.add_argument("--offline", action="store_true", help="scripted model + local synthetic web (no keys)")
    r.set_defaults(fn=cmd_research)
    b = sub.add_parser("bench", help="run the benchmark on the offline synthetic web")
    b.add_argument("--questions", default="benchmark/questions.jsonl")
    b.add_argument("--out", default="benchmark/results")
    b.add_argument("--offline", action="store_true", help="use the scripted model (pipeline smoke test)")
    b.set_defaults(fn=cmd_bench)
    sv = sub.add_parser("serve", help="run the web console + API")
    sv.add_argument("--host", default="0.0.0.0")
    sv.add_argument("--port", type=int, default=8000)
    sv.set_defaults(fn=cmd_serve)
    args = p.parse_args(argv)
    args.fn(args)


if __name__ == "__main__":
    main()
