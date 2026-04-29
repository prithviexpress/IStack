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

app = FastAPI(title="IdeaStack")

_STATIC = Path(__file__).parent.parent / "static"


@app.get("/", response_class=HTMLResponse)
async def index():
    return HTMLResponse((_STATIC / "index.html").read_text())


class BrainstormRequest(BaseModel):
    seed_idea: str


def _build_context(outputs: list[dict], seed_idea: str) -> str:
    if not outputs:
        return ""
    lines = [f"## ORIGINAL SEED IDEA\n{seed_idea}\n", "---", "## PANEL ANALYSIS SO FAR"]
    for e in outputs:
        lines.append(f"\n### {e['emoji']} {e['name']} — {e['role']}\n")
        lines.append(e["output"])
    return "\n".join(lines)


def _run_brainstorm(seed_idea: str, api_key: str, emit) -> None:
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

        ctx = _build_context(outputs, seed_idea)
        if ctx:
            user_msg = (
                f"## ORIGINAL SEED IDEA\n{seed_idea}\n\n---\n\n{ctx}\n\n---\n\n"
                "Now provide your analysis of the seed idea above, informed by the panel's work so far."
            )
        else:
            user_msg = f"## SEED IDEA\n\n{seed_idea}"

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

    def worker() -> None:
        _run_brainstorm(req.seed_idea, api_key, emit)
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
