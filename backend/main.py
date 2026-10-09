import asyncio
import contextlib
import json
import queue
import threading

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from services.environment_orchestrator import run_environment


app = FastAPI(title="AI K8s Twin API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class EnvironmentRunRequest(BaseModel):
    github_url: str = Field(min_length=1)
    cpu: str = Field(min_length=1)
    memory: str = Field(min_length=1)
    vus: str = Field(min_length=1)
    duration: str = Field(min_length=1)
    environments: list[str] = Field(min_length=1)


def _sse(event, data):
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


class _QueueWriter:
    def __init__(self, events):
        self.events = events

    def write(self, text):
        if text:
            self.events.put(("output", {"text": text}))
        return len(text)

    def flush(self):
        return None


@app.post("/api/environment-orchestrator/run")
async def start_environment_orchestrator(request: EnvironmentRunRequest):
    events = queue.Queue()
    sentinel = object()

    def execute():
        output = _QueueWriter(events)
        try:
            with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
                result = run_environment(**request.model_dump())
            events.put(("result", {"status": "completed", "result": result}))
        except Exception as error:
            events.put(("error", {"status": "failed", "message": str(error)}))
        finally:
            events.put(sentinel)

    threading.Thread(target=execute, daemon=True).start()

    async def stream():
        while True:
            try:
                event = await asyncio.to_thread(events.get)
            except asyncio.CancelledError:
                return
            if event is sentinel:
                yield _sse("done", {"status": "finished"})
                return
            yield _sse(event[0], event[1])

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )