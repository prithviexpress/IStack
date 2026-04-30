import json
import os
import threading
import time
from pathlib import Path

import anthropic
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel

from .agents import AGENTS
from .utils import format_context

app = FastAPI(title="IdeaStack")

_STATIC = Path(__file__).parent.parent / "static"


@app.get("/", response_class=HTMLResponse)
async def index():
    return HTMLResponse((_STATIC / "index.html").read_text())


class BrainstormContext(BaseModel):
    country: str = ""
    industry: str = ""
    stage: str = ""
    target_audience: str = ""
    notes: str = ""


class BrainstormRequest(BaseModel):
    seed_idea: str
    context: BrainstormContext = BrainstormContext()


class DiscussMessage(BaseModel):
    role: str   # "user" or "assistant"
    text: str


class DiscussRequest(BaseModel):
    agent_index: int
    seed_idea: str
    context: BrainstormContext = BrainstormContext()
    agent_analysis: str
    thread: list[DiscussMessage] = []   # completed exchanges before this question
    question: str


class RunAgentRequest(BaseModel):
    agent_index: int
    seed_idea: str
    context: BrainstormContext = BrainstormContext()
    prior_outputs: list[dict] = []


def _build_panel_context(outputs: list[dict], seed_idea: str) -> str:
    if not outputs:
        return ""
    lines = [f"## ORIGINAL SEED IDEA\n{seed_idea}\n", "---", "## PANEL ANALYSIS SO FAR"]
    for e in outputs:
        lines.append(f"\n### {e['emoji']} {e['name']} — {e['role']}\n")
        lines.append(e["output"])
    return "\n".join(lines)


def _run_brainstorm(seed_idea: str, api_key: str, emit, ctx_block: str = "") -> None:
    client = anthropic.Anthropic(api_key=api_key)
    outputs: list[dict] = []

    for i, agent in enumerate(AGENTS):
        emit("agent_start", {
            "index": i,
            "name": agent.name,
            "emoji": agent.emoji,
            "color": agent.color,
            "role": agent.role,
            "total": len(AGENTS),
        })

        panel_ctx = _build_panel_context(outputs, seed_idea)
        if panel_ctx:
            user_msg = (
                f"## ORIGINAL SEED IDEA\n{seed_idea}\n\n"
                + (f"{ctx_block}\n\n" if ctx_block else "")
                + f"---\n\n{panel_ctx}\n\n---\n\n"
                + "Now provide your analysis of the seed idea above, informed by the panel's work so far."
            )
        else:
            user_msg = f"## SEED IDEA\n\n{seed_idea}"
            if ctx_block:
                user_msg += f"\n\n{ctx_block}"

        kwargs: dict = {
            "model": "claude-opus-4-7",
            "max_tokens": agent.max_tokens,
            "system": [
                {
                    "type": "text",
                    "text": agent.system_prompt,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            "messages": [{"role": "user", "content": user_msg}],
            "output_config": {"effort": agent.effort},
        }
        if agent.use_thinking:
            kwargs["thinking"] = {"type": "adaptive", "display": "summarized"}

        buf: list[str] = []
        t0 = time.time()
        try:
            with client.messages.stream(**kwargs) as stream:
                for text in stream.text_stream:
                    buf.append(text)
                    emit("token", {"index": i, "text": text})
        except Exception as exc:
            emit("error", {"index": i, "message": str(exc)})
            return

        outputs.append({
            "name": agent.name,
            "emoji": agent.emoji,
            "role": agent.role,
            "color": agent.color,
            "output": "".join(buf),
        })
        emit("agent_end", {"index": i, "duration_s": round(time.time() - t0, 1)})

    emit("complete", {"total": len(AGENTS)})


@app.post("/brainstorm")
async def brainstorm(req: BrainstormRequest):
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise HTTPException(status_code=500, detail="ANTHROPIC_API_KEY not configured")

    import asyncio
    loop = asyncio.get_running_loop()
    q: asyncio.Queue = asyncio.Queue()

    def emit(event: str, data: dict) -> None:
        loop.call_soon_threadsafe(q.put_nowait, (event, data))

    ctx_block = format_context(req.context.model_dump())

    def worker() -> None:
        _run_brainstorm(req.seed_idea, api_key, emit, ctx_block)
        loop.call_soon_threadsafe(q.put_nowait, None)

    threading.Thread(target=worker, daemon=True).start()

    async def generate():
        while True:
            item = await q.get()
            if item is None:
                break
            event, data = item
            yield f"event: {event}\ndata: {json.dumps(data)}\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _sse_stream(worker_fn) -> StreamingResponse:
    """Shared SSE scaffold: run worker_fn(emit) in a thread, stream events."""
    import asyncio
    loop = asyncio.get_running_loop()
    q: asyncio.Queue = asyncio.Queue()

    def emit(event: str, data: dict) -> None:
        loop.call_soon_threadsafe(q.put_nowait, (event, data))

    def _worker() -> None:
        try:
            worker_fn(emit)
        finally:
            loop.call_soon_threadsafe(q.put_nowait, None)

    threading.Thread(target=_worker, daemon=True).start()

    async def generate():
        while True:
            item = await q.get()
            if item is None:
                break
            event, data = item
            yield f"event: {event}\ndata: {json.dumps(data)}\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/discuss")
async def discuss(req: DiscussRequest):
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise HTTPException(status_code=500, detail="ANTHROPIC_API_KEY not configured")
    if not 0 <= req.agent_index < len(AGENTS):
        raise HTTPException(status_code=400, detail="Invalid agent_index")

    agent = AGENTS[req.agent_index]
    ctx_block = format_context(req.context.model_dump())

    preamble = f"## SEED IDEA\n\n{req.seed_idea}"
    if ctx_block:
        preamble += f"\n\n{ctx_block}"
    preamble += f"\n\n## YOUR ORIGINAL ANALYSIS\n\n{req.agent_analysis}"

    if not req.thread:
        messages = [{"role": "user", "content": preamble + f"\n\n## FOLLOW-UP\n\n{req.question}"}]
    else:
        messages = [{"role": "user", "content": preamble + f"\n\n## FOLLOW-UP\n\n{req.thread[0].text}"}]
        for msg in req.thread[1:]:
            messages.append({"role": msg.role, "content": msg.text})
        messages.append({"role": "user", "content": req.question})

    def worker(emit) -> None:
        client = anthropic.Anthropic(api_key=api_key)
        try:
            with client.messages.stream(
                model="claude-opus-4-7",
                max_tokens=2048,
                system=agent.system_prompt,
                messages=messages,
            ) as stream:
                for text in stream.text_stream:
                    emit("token", {"text": text})
            emit("complete", {})
        except Exception as exc:
            emit("error", {"message": str(exc)})

    return _sse_stream(worker)


@app.post("/run-agent")
async def run_agent(req: RunAgentRequest):
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise HTTPException(status_code=500, detail="ANTHROPIC_API_KEY not configured")
    if not 0 <= req.agent_index < len(AGENTS):
        raise HTTPException(status_code=400, detail="Invalid agent_index")

    agent = AGENTS[req.agent_index]
    ctx_block = format_context(req.context.model_dump())

    if req.prior_outputs:
        panel_ctx = _build_panel_context(req.prior_outputs, req.seed_idea)
        user_msg = (
            f"## ORIGINAL SEED IDEA\n{req.seed_idea}\n\n"
            + (f"{ctx_block}\n\n" if ctx_block else "")
            + f"---\n\n{panel_ctx}\n\n---\n\n"
            + "Now provide your analysis of the seed idea above, informed by the panel's work so far."
        )
    else:
        user_msg = f"## SEED IDEA\n\n{req.seed_idea}"
        if ctx_block:
            user_msg += f"\n\n{ctx_block}"

    def worker(emit) -> None:
        client = anthropic.Anthropic(api_key=api_key)
        emit("agent_start", {
            "index": req.agent_index,
            "name": agent.name,
            "emoji": agent.emoji,
            "color": agent.color,
            "role": agent.role,
            "total": 1,
        })
        kwargs: dict = {
            "model": "claude-opus-4-7",
            "max_tokens": agent.max_tokens,
            "system": [{"type": "text", "text": agent.system_prompt, "cache_control": {"type": "ephemeral"}}],
            "messages": [{"role": "user", "content": user_msg}],
            "output_config": {"effort": agent.effort},
        }
        if agent.use_thinking:
            kwargs["thinking"] = {"type": "adaptive", "display": "summarized"}
        t0 = time.time()
        try:
            with client.messages.stream(**kwargs) as stream:
                for text in stream.text_stream:
                    emit("token", {"index": req.agent_index, "text": text})
        except Exception as exc:
            emit("error", {"index": req.agent_index, "message": str(exc)})
            return
        emit("agent_end", {"index": req.agent_index, "duration_s": round(time.time() - t0, 1)})
        emit("complete", {"total": 1})

    return _sse_stream(worker)
