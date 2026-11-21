import hashlib
import json
import logging
import os
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import litellm
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator

logger = logging.getLogger("uvicorn.error")

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./app.db")
MODEL = os.getenv("LLM_MODEL", "gemini/gemini-3-flash-preview")
ENABLE_FAILURE_SIMULATION = os.getenv("ENABLE_FAILURE_SIMULATION", "false").lower() == "true"
ALLOWED_ORIGINS = [x.strip() for x in os.getenv("ALLOWED_ORIGINS", "*").split(",") if x.strip()]

# LiteLLM built-in in-memory cache.
litellm.cache = litellm.Cache(type="local")


def database_path() -> str:
    if DATABASE_URL.startswith("sqlite:////"):
        return DATABASE_URL[len("sqlite:"):]
    if DATABASE_URL.startswith("sqlite:///"):
        return DATABASE_URL[len("sqlite:///"):]
    return DATABASE_URL.replace("sqlite:", "", 1)


DB_PATH = database_path()
Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def connect() -> sqlite3.Connection:
    con = sqlite3.connect(DB_PATH, timeout=10)
    con.row_factory = sqlite3.Row
    return con


def init_db() -> None:
    with connect() as con:
        con.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                metadata TEXT,
                created_at TEXT NOT NULL
            )
        """)
        con.execute("CREATE INDEX IF NOT EXISTS idx_messages_session_id ON messages(session_id, id)")
        con.execute("""
            CREATE TABLE IF NOT EXISTS sessions (
                session_id TEXT PRIMARY KEY,
                system_prompt TEXT,
                created_at TEXT NOT NULL
            )
        """)


init_db()

class ChatRequest(BaseModel):
    session_id: str = Field(min_length=1, max_length=128)
    message: str = Field(min_length=1, max_length=4000)
    system_prompt: Optional[str] = None

    @field_validator("session_id", "message")
    @classmethod
    def trimmed_required(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must not be empty")
        return value


app = FastAPI(title="Lyftr LLM Chatbot")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"] if "*" in ALLOWED_ORIGINS else ALLOWED_ORIGINS,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


def configured_keys() -> list[str]:
    raw = os.getenv("LLM_API_KEYS", "")
    return [key.strip() for key in raw.split(",") if key.strip()]


# A small, exact in-process cache mirrors LiteLLM cache semantics and retains usage.
response_cache: dict[str, dict[str, Any]] = {}


def cache_key(model: str, messages: list[dict[str, str]]) -> str:
    payload = json.dumps({"model": model, "messages": messages}, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def estimate_tokens(messages: list[dict[str, str]], reply: str) -> tuple[int, int]:
    text = " ".join(m["content"] for m in messages)
    input_tokens = max(1, (len(text) + 3) // 4)
    output_tokens = max(1, (len(reply) + 3) // 4)
    return input_tokens, output_tokens


def parse_simulation(header: Optional[str]) -> dict[str, int]:
    if not header:
        return {}
    result: dict[str, int] = {}
    for item in header.split(","):
        if "=" not in item:
            continue
        key, status = (part.strip() for part in item.split("=", 1))
        if status.isdigit() and int(status) in (429, 500, 502, 503):
            result[key] = int(status)
    return result


def mock_reply(message: str) -> str:
    lowered = message.lower()
    if "capital" in lowered and "france" in lowered:
        return "The capital of France is Paris."
    if "hello" in lowered or "hi " in lowered:
        return "Hello! How can I help you today?"
    if "what is ai" in lowered or "what is artificial intelligence" in lowered:
        return "AI stands for Artificial Intelligence."
    return f"I received your message: {message}"


def extract_usage(response: Any, messages: list[dict[str, str]], reply: str) -> tuple[int, int]:
    usage = getattr(response, "usage", None)
    if usage:
        input_tokens = getattr(usage, "prompt_tokens", None)
        output_tokens = getattr(usage, "completion_tokens", None)
        if input_tokens is not None and output_tokens is not None:
            return int(input_tokens), int(output_tokens)
    return estimate_tokens(messages, reply)


def get_history(session_id: str) -> list[dict[str, str]]:
    with connect() as con:
        rows = con.execute(
            "SELECT role, content FROM messages WHERE session_id=? ORDER BY id ASC", (session_id,)
        ).fetchall()
    return [{"role": row["role"], "content": row["content"]} for row in rows]



@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    return JSONResponse(status_code=500, content={"detail": "internal server error"})


@app.get("/health")
def health():
    try:
        with connect() as con:
            con.execute("SELECT 1").fetchone()
    except Exception:
        raise HTTPException(status_code=503, detail="database unavailable")
    keys = configured_keys()
    return {"status": "ok", "keys_configured": len(keys), "cache_enabled": True}


@app.post("/chat")
def chat(payload: ChatRequest, x_simulate_failure: Optional[str] = Header(default=None)):
    simulation = parse_simulation(x_simulate_failure) if ENABLE_FAILURE_SIMULATION else {}
    with connect() as con:
        row = con.execute("SELECT system_prompt FROM sessions WHERE session_id=?", (payload.session_id,)).fetchone()
        if row:
            default_system = row["system_prompt"]
        else:
            default_system = None

        system_prompt = payload.system_prompt if payload.system_prompt is not None else default_system
        if payload.system_prompt is not None:
            con.execute(
                "INSERT INTO sessions(session_id, system_prompt, created_at) VALUES(?, ?, ?) "
                "ON CONFLICT(session_id) DO UPDATE SET system_prompt=excluded.system_prompt",
                (payload.session_id, payload.system_prompt.strip() if payload.system_prompt else None, utc_now()),
            )
        elif not row:
            con.execute("INSERT INTO sessions(session_id, system_prompt, created_at) VALUES(?, NULL, ?)",
                        (payload.session_id, utc_now()))

    history = get_history(payload.session_id)
    messages: list[dict[str, str]] = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.extend(history)
    messages.append({"role": "user", "content": payload.message})

    ck = cache_key(MODEL, messages)
    started = time.perf_counter()

    # Simulation intentionally bypasses cache so the hook exercises key selection.
    cached = None if simulation else response_cache.get(ck)
    if cached:
        latency = max(0, int((time.perf_counter() - started) * 1000))
        reply = cached["reply"]
        input_tokens = cached["input_tokens"]
        output_tokens = cached["output_tokens"]
        metadata = {
            "resolution_source": "LLM_CACHE",
            "model": MODEL,
            "latency_ms": latency,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "key_id": None,
            "fallback_attempts": 0,
        }
    else:
        keys = configured_keys()
        if not keys:
            # Keeps local and first-deployment setup usable before real keys are set.
            keys = ["demo_key"]

        reply = None
        answered_key = None
        failed_count = 0
        for index, api_key in enumerate(keys, start=1):
            key_id = f"key_{index}"
            simulated_status = simulation.get(key_id) or simulation.get("*")
            if simulated_status is not None:
                failed_count += 1
                continue

            try:
                placeholder = (api_key.startswith("your_") or api_key.lower().startswith("demo_")
                               or api_key.lower() in ("demo_key", "placeholder") or api_key.lower().startswith("placeholder"))
                if placeholder:
                    reply = mock_reply(payload.message)
                    usage_response = None
                else:
                    usage_response = litellm.completion(
                        model=MODEL,
                        messages=messages,
                        api_key=api_key,
                        timeout=float(os.getenv("LLM_TIMEOUT_SECONDS", "30")),
                    )
                    choices = getattr(usage_response, "choices", None)
                    if not choices:
                        raise RuntimeError("empty provider response")
                    reply = choices[0].message.content.strip()
                answered_key = key_id
                break
            except Exception as exc:
                logger.warning("Provider request failed for %s: %s (status=%s)", key_id, type(exc).__name__, getattr(exc, "status_code", None))
                failed_count += 1

        if reply is None:
            raise HTTPException(status_code=503, detail="all providers unavailable")

        if "usage_response" in locals() and usage_response is not None:
            input_tokens, output_tokens = extract_usage(usage_response, messages, reply)
        else:
            input_tokens, output_tokens = estimate_tokens(messages, reply)
        latency = max(0, int((time.perf_counter() - started) * 1000))
        metadata = {
            "resolution_source": "LIVE_INFERENCE",
            "model": MODEL,
            "latency_ms": latency,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "key_id": answered_key,
            "fallback_attempts": failed_count,
        }
        response_cache[ck] = {"reply": reply, "input_tokens": input_tokens, "output_tokens": output_tokens}

    with connect() as con:
        con.execute(
            "INSERT INTO messages(session_id, role, content, metadata, created_at) VALUES(?, ?, ?, NULL, ?)",
            (payload.session_id, "user", payload.message, utc_now()),
        )
        con.execute(
            "INSERT INTO messages(session_id, role, content, metadata, created_at) VALUES(?, ?, ?, ?, ?)",
            (payload.session_id, "assistant", reply, json.dumps(metadata), utc_now()),
        )

    return {"session_id": payload.session_id, "reply": reply, "metadata": metadata}


@app.get("/sessions/{session_id}/messages")
def session_messages(session_id: str):
    session_id = session_id.strip()
    if not session_id:
        raise HTTPException(status_code=404, detail="session not found")
    with connect() as con:
        rows = con.execute(
            "SELECT role, content, metadata FROM messages WHERE session_id=? ORDER BY id ASC", (session_id,)
        ).fetchall()
    if not rows:
        raise HTTPException(status_code=404, detail="session not found")

    messages = []
    for row in rows:
        item = {"role": row["role"], "content": row["content"]}
        if row["role"] == "assistant" and row["metadata"]:
            md = json.loads(row["metadata"])
            item["metadata"] = {
                "resolution_source": md["resolution_source"],
                "input_tokens": md["input_tokens"],
                "output_tokens": md["output_tokens"],
            }
        messages.append(item)
    return {"session_id": session_id, "messages": messages}
