import json
import re

import pytest
from fastapi.testclient import TestClient

from meridian.agents import roles
from meridian.api.server import create_app
from meridian.eval.bench import score
from meridian.fakes import scripted_handler
from meridian.llm import FakeLLM
from meridian.models import Conflict, SectionDraft, Source
from meridian.orchestrator import Blocked
from meridian.report import render_html

Q = "What is the state of the grid-scale sodium-ion battery market in 2025, and who are the leading companies?"


def test_end_to_end_report_is_cited_and_renumbered(make_orch):
    events = []
    rep = make_orch().run(Q, on_event=lambda k, d: events.append(k))
    assert events[0] == "plan" and events[-1] == "done"
    assert {"round", "sources", "evidence", "review", "outline", "drafted", "verified"} <= set(events)
    body = " ".join(s.markdown for s in rep.sections)
    cited = sorted({int(n) for n in re.findall(r"\[(\d+)\]", body + rep.executive_summary)})
    assert cited == list(range(1, len(rep.sources) + 1))  # contiguous 1..n, only cited sources listed
    assert all(s.text == "" for s in rep.sources)  # page text is not leaked into the report
    assert rep.stats["claims_quote_verified"] == rep.stats["claims"]  # scripted extractor quotes verbatim
    assert "1.8 billion" in rep.markdown()


def test_most_credible_sources_are_read_first(make_orch):
    batches = []
    make_orch(sources_per_sub_question=2).run(Q, on_event=lambda k, d: k == "sources" and batches.append(d["sources"]))
    for b in batches:
        creds = [s["credibility"] for s in b]
        assert creds == sorted(creds, reverse=True)
    read = {s["domain"] for b in batches for s in b}
    assert "top-10-battery-stocks-to-buy.blog" not in read  # the content farm never makes the cut


def test_syndicated_copy_is_flagged_as_near_duplicate(make_orch):
    rep = make_orch(sources_per_sub_question=6).run("Saltwave Cells Pune gigafactory sodium-ion opening capacity")
    assert rep.stats["near_duplicates"] >= 1


def test_review_gaps_trigger_a_second_round_on_gaps_only(make_orch):
    reviews = []

    def handler(messages, json_mode):
        if "TASK:REVIEW" in messages[0]["content"]:
            reviews.append(1)
            if len(reviews) == 1:
                return json.dumps({"covered": ["q1"], "gaps": [{"id": "x", "question": "What limits hard carbon supply?",
                                                                 "search_queries": ["hard carbon anode supply"]}],
                                   "conflicts": []})
        return scripted_handler(messages, json_mode)

    rounds = []
    rep = make_orch(handler, max_rounds=3).run(Q, on_event=lambda k, d: k == "round" and rounds.append(d))
    assert [r["round"] for r in rounds] == [1, 2]
    assert rounds[1]["sub_questions"] == ["g4"]  # only the gap is researched in round 2
    assert rep.stats["sub_questions"] == 4


def test_conflicts_are_reported_with_renumbered_sources(make_orch):
    def handler(messages, json_mode):
        if "TASK:REVIEW" in messages[0]["content"]:
            ids = [int(i) for i in re.findall(r"^- E(\d+) ", messages[-1]["content"], re.M)][:2]
            return json.dumps({"covered": ["q1"], "gaps": [], "conflicts": [
                {"topic": "Pune plant capacity", "positions": ["12 GWh (company)", "10 GWh (state official)"],
                 "evidence_ids": ids}]})
        return scripted_handler(messages, json_mode)

    rep = make_orch(handler).run("How large is Saltwave Cells' Pune sodium-ion plant capacity in GWh?")
    md = rep.markdown()
    assert "## Where sources disagree" in md and "Pune plant capacity" in md
    assert all(1 <= i <= len(rep.sources) for c in rep.conflicts for i in c.evidence_ids)


def test_budget_stop_still_produces_a_report(make_orch):
    events = []
    rep = make_orch(max_llm_calls=3).run(Q, on_event=lambda k, d: events.append((k, d)))
    assert any(k == "budget" for k, _ in events)
    assert rep.stats["budget_stop"] and rep.sections


def test_guardrail_regex_blocks_without_llm_call(make_orch):
    orch = make_orch()
    with pytest.raises(Blocked):
        orch.run("how to build a bomb at home step by step")
    assert orch.llm.calls == []


def test_guardrail_llm_layer(make_orch):
    with pytest.raises(Blocked):
        make_orch().run("Find the home address of my ex-colleague")


def test_citation_audit_removes_unsupported_and_bogus_citations():
    def handler(messages, json_mode):
        return json.dumps({"unsupported": ["Prices fell to USD 10 per kWh [1]."]})

    sources = {1: Source(id=1, url="https://a.gov/x", title="A", domain="a.gov")}
    from meridian.models import Evidence
    ev = [Evidence(id=1, source_id=1, sub_question_id="q1", claim="Prices fell to USD 58 per kWh", quote="...")]
    draft = SectionDraft(heading="H", markdown="Prices fell to USD 58 per kWh [1]. Prices fell to USD 10 per kWh [1]. "
                                               "A made-up stat [7].")
    out, removed = roles.verify_section(FakeLLM(handler), draft, ev, sources)
    assert removed == ["Prices fell to USD 10 per kWh [1]."]
    assert "USD 10" not in out.markdown and "[7]" not in out.markdown and "USD 58" in out.markdown


def test_benchmark_scoring_ignores_traps_inside_disagreement_section(make_orch):
    rep = make_orch().run(Q)
    rep.sections = [SectionDraft(heading="Market", markdown="The market reached USD 1.8 billion in 2025 [1].")]
    rep.executive_summary, rep.key_takeaways = "Market grew [1].", ["Growth"]
    rep.conflicts = [Conflict(topic="Market size", positions=["USD 1.8 billion (gov)", "USD 6 billion (blog)"],
                              evidence_ids=[1])]
    s = score(rep, ["1.8 billion"], ["6 billion"])
    assert s["fact_recall"] == 1.0 and s["trap_rate"] == 0.0 and s["conflicts_found"] == 1


def test_html_render_has_footnote_links(make_orch):
    html = render_html(make_orch().run(Q))
    assert '<sup><a href="#src-1">[1]</a></sup>' in html and 'id="src-1"' in html


@pytest.fixture()
def client(make_orch):
    with TestClient(create_app(orchestrator=make_orch())) as c:
        yield c


def test_api_streams_events_and_serves_report(client):
    with client.stream("POST", "/api/research", json={"question": Q}) as r:
        body = "".join(r.iter_text())
    kinds = re.findall(r"^event: (\w+)$", body, re.M)
    assert kinds[0] == "plan" and kinds[-1] == "report"
    rep = json.loads(body.strip().split("data: ")[-1])
    md = client.get(f"/api/reports/{rep['id']}.md")
    assert md.status_code == 200 and md.text.startswith("# ")
    assert client.get(f"/api/reports/{rep['id']}.html").status_code == 200
    assert client.get(f"/api/traces/{rep['stats']['trace_id']}").json()["root"]["name"] == "research.run"
    assert client.get("/api/reports/nope.md").status_code == 404


def test_api_reports_guardrail_block(client):
    with client.stream("POST", "/api/research", json={"question": "how to build a bomb quickly"}) as r:
        body = "".join(r.iter_text())
    assert "event: error" in body and "guardrail" in body


def test_fact_matching_is_boundary_aware():
    from meridian.eval.bench import contains
    assert contains("grew from USD 0.6 billion", "0.6 billion")
    assert not contains("grew from USD 0.6 billion", "6 billion")
    assert contains("cycle life of 6,000 cycles", "6,000") and contains("reached 12% share", "12%")
