"""Render a Report to Markdown or standalone HTML."""
from __future__ import annotations

import html
import re
from datetime import date

from .models import Report


def render_markdown(r: Report) -> str:
    out = [f"# {r.title}", "", f"*Research question:* {r.question}  ", f"*Generated:* {date.today().isoformat()} · "
           f"{len(r.sources)} sources cited · {r.stats.get('sources_considered', '?')} considered", "",
           "## Executive summary", "", r.executive_summary, "", "**Key takeaways**", ""]
    out += [f"- {t}" for t in r.key_takeaways]
    for s in r.sections:
        out += ["", f"## {s.heading}", "", s.markdown]
    if r.conflicts:
        out += ["", "## Where sources disagree", ""]
        for c in r.conflicts:
            cites = "".join(f"[{i}]" for i in c.evidence_ids)
            out.append(f"- **{c.topic}** {cites}")
            out += [f"  - {p}" for p in c.positions]
    out += ["", "## Sources", ""]
    for s in r.sources:
        pub = f", {s.published}" if s.published else ""
        out.append(f"{s.id}. [{s.title or s.url}]({s.url}) — {s.domain}{pub} · credibility {s.credibility:.2f} "
                   f"({'; '.join(s.credibility_reasons)})")
    st = r.stats
    out += ["", "---", f"<sub>Method: {st.get('sub_questions', '?')} sub-questions · {st.get('rounds', '?')} research "
            f"round(s) · {st.get('searches', '?')} searches · {st.get('pages_fetched', '?')} pages read · "
            f"{st.get('claims', '?')} claims extracted ({st.get('claims_quote_verified', '?')} with verified quotes) · "
            f"{st.get('unsupported_removed', 0)} unsupported sentences removed by the citation audit · "
            f"{st.get('llm_calls', '?')} LLM calls · ${st.get('cost_usd', 0)} · {st.get('seconds', '?')}s</sub>"]
    return "\n".join(out) + "\n"


def _inline(md: str) -> str:
    t = html.escape(md)
    t = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", t)
    t = re.sub(r"\*(.+?)\*", r"<em>\1</em>", t)
    t = re.sub(r"\[([^\]]+)\]\((https?://[^)]+)\)", r'<a href="\2">\1</a>', t)
    return re.sub(r"\[(\d+)\]", r'<sup><a href="#src-\1">[\1]</a></sup>', t)


def markdown_to_html(md: str) -> str:
    """Minimal Markdown → HTML (headings, lists, tables, paragraphs), enough for reports."""
    lines, out, in_ul, in_table = md.splitlines(), [], False, False
    for line in lines:
        if line.startswith("|"):
            cells = [c.strip() for c in line.strip("|").split("|")]
            if all(re.fullmatch(r":?-+:?", c) for c in cells):
                continue
            if not in_table:
                out.append("<table>")
                in_table = True
            out.append("<tr>" + "".join(f"<td>{_inline(c)}</td>" for c in cells) + "</tr>")
            continue
        if in_table:
            out.append("</table>")
            in_table = False
        m = re.match(r"^(\s*)[-*] (.*)", line)
        if m:
            if not in_ul:
                out.append("<ul>")
                in_ul = True
            out.append(f"<li>{_inline(m.group(2))}</li>")
            continue
        if in_ul:
            out.append("</ul>")
            in_ul = False
        h = re.match(r"^(#{1,3}) (.*)", line)
        if h:
            out.append(f"<h{len(h.group(1))}>{_inline(h.group(2))}</h{len(h.group(1))}>")
        elif re.match(r"^\d+\. ", line):
            n = line.split(".")[0]
            out.append(f'<p id="src-{n}" class="src">{_inline(line)}</p>')
        elif line.strip() == "---":
            out.append("<hr>")
        elif line.strip():
            out.append(f"<p>{_inline(line)}</p>")
    if in_ul:
        out.append("</ul>")
    if in_table:
        out.append("</table>")
    return "\n".join(out)


def render_html(r: Report) -> str:
    body = markdown_to_html(render_markdown(r)).replace("&lt;sub&gt;", "<sub>").replace("&lt;/sub&gt;", "</sub>")
    return f"""<!doctype html><html><head><meta charset="utf-8"><title>{html.escape(r.title)}</title>
<style>body{{max-width:780px;margin:40px auto;padding:0 20px;font:16px/1.65 Georgia,serif;color:#1d1d1f}}
h1{{font-size:30px;line-height:1.2}}h2{{margin-top:2em;border-bottom:1px solid #ddd;padding-bottom:4px}}
sup a{{text-decoration:none;color:#0a6}}table{{border-collapse:collapse}}td{{border:1px solid #ddd;padding:4px 8px}}
.src{{font-size:14px;color:#444}}sub{{color:#666}}</style></head><body>{body}</body></html>"""
