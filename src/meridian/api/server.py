"""FastAPI service with a live research console.

POST /api/research            {question} -> SSE stream of agent events, ending with `report`
GET  /api/reports/{id}.md     download a finished report (also .html)
GET  /api/traces/{id}         span tree with timings, tokens and cost
GET  /                        web console
"""
from __future__ import annotations

import asyncio
import json
import threading
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, PlainTextResponse, StreamingResponse
from pydantic import BaseModel, Field

from ..config import Settings, get_settings
from ..orchestrator import Blocked, Orchestrator, build_orchestrator
from ..report import render_html
from ..tracing import STORE

STATIC = Path(__file__).parent / "static"


class ResearchRequest(BaseModel):
    question: str = Field(min_length=8, max_length=1000)


def create_app(settings: Settings | None = None, orchestrator: Orchestrator | None = None) -> FastAPI:
    s = settings or get_settings()
    state: dict = {"orch": orchestrator, "reports": {}}

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if state["orch"] is None:
            if s.demo:
                from ..llm import FakeLLM

                s.search_provider = "local"
                state["orch"] = build_orchestrator(s, llm=FakeLLM(model="offline scripted demo", delay=0.02))
            else:
                state["orch"] = build_orchestrator(s)
        STORE.path = s.trace_file
        yield

    app = FastAPI(title="Meridian", version="1.0.0", lifespan=lifespan)

    @app.get("/api/health")
    def health():
        o: Orchestrator = state["orch"]
        return {"status": "ok", "model": o.llm.model, "search": o.search.name}

    @app.post("/api/research")
    async def research(req: ResearchRequest):
        loop = asyncio.get_running_loop()
        q: asyncio.Queue = asyncio.Queue()
        done = object()
        report_id = uuid.uuid4().hex[:10]

        def emit(kind: str, data: dict) -> None:
            loop.call_soon_threadsafe(q.put_nowait, (kind, data))

        def worker():
            try:
                rep = state["orch"].run(req.question, on_event=emit)
                state["reports"][report_id] = rep
                emit("report", {"id": report_id, "markdown": rep.markdown(), "stats": rep.stats})
            except Blocked as e:
                emit("error", {"error": f"Request blocked by guardrail: {e}"})
            except Exception as e:
                emit("error", {"error": f"{type(e).__name__}: {e}"})
            finally:
                loop.call_soon_threadsafe(q.put_nowait, done)

        threading.Thread(target=worker, daemon=True).start()

        async def stream():
            while True:
                item = await q.get()
                if item is done:
                    break
                kind, data = item
                yield f"event: {kind}\ndata: {json.dumps(data, default=str)}\n\n"

        return StreamingResponse(stream(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @app.get("/api/reports/{rid}.md", response_class=PlainTextResponse)
    def report_md(rid: str):
        rep = state["reports"].get(rid) or _404()
        return PlainTextResponse(rep.markdown(), headers={"Content-Disposition": f'attachment; filename="{rid}.md"'})

    @app.get("/api/reports/{rid}.html")
    def report_html(rid: str):
        from fastapi.responses import HTMLResponse

        rep = state["reports"].get(rid) or _404()
        return HTMLResponse(render_html(rep))

    @app.get("/api/traces/{tid}")
    def trace(tid: str):
        t = STORE.get(tid) or _404()
        return t.to_dict()

    @app.get("/")
    def index():
        return FileResponse(STATIC / "index.html")

    return app


def _404():
    raise HTTPException(404, "not found")
