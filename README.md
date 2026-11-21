# Lyftr AI LLM Chatbot

## 1. Live URLs
- Frontend: add your public frontend URL here.
- Backend: add your public backend URL here.
- Repository: add your public GitHub repository URL here.

## 2. Run locally: with Docker and without
### With Docker
Copy `.env.example` to `.env`, add your Gemini keys, then run:

```bash
make up
```

Open `http://localhost:3000`. The API is at `http://localhost:8000` and the SQLite volume is named `lyftr-data`.

```bash
make down
```

### Without Docker
```bash
cd backend
python -m venv .venv
.venv/Scripts/activate   # Windows
pip install -r requirements.txt
set DATABASE_URL=sqlite:///./app.db
set LLM_API_KEYS=key1,key2
set ENABLE_FAILURE_SIMULATION=true
uvicorn main:app --reload --port 8000

cd ../frontend
npm install
set VITE_API_URL=http://localhost:8000
npm run dev -- --port 3000
```

Run backend tests:

```bash
cd backend
pytest -q
```

## 3. Design decisions: key fallback, caching, SQLite session storage
FastAPI exposes exactly `/health`, `/chat`, and `/sessions/{session_id}/messages`. LiteLLM is configured with a local built-in cache and calls the Gemini model through each configured API key. The backend tries keys in order and records only provider-failure fallback attempts. An in-process cache additionally retains the reply and original token usage by an exact model-plus-full-message-list hash. SQLite stores user and assistant rows; an optional sessions table holds the session system prompt while the history endpoint excludes it.

## 4. Verification: how to see failover, cache hits and per-message token usage
Send the same message in two new sessions. The first response should be `LIVE_INFERENCE`; the second with identical system prompt and history should be `LLM_CACHE` and contain the same reply. Send these failover requests:

```bash
curl -X POST http://localhost:8000/chat -H "Content-Type: application/json" -H "X-Simulate-Failure: key_1=429" -d '{"session_id":"demo","message":"Hello"}'
curl -X POST http://localhost:8000/chat -H "Content-Type: application/json" -H "X-Simulate-Failure: *=429" -d '{"session_id":"failed","message":"Hello"}'
```

Check history with `GET /sessions/demo/messages`. Input and output tokens are shown beneath every frontend assistant reply.

## 5. Setup Used
Built in VS Code on Windows using Git, Docker Desktop, React/Vite, FastAPI, LiteLLM, SQLite, and AI-assisted implementation.

## 6. Bonus features (if any)
- Deterministic local demo replies when only placeholder/demo keys are configured, useful for UI and Docker verification.
- System prompt persistence per session.
- Responsive chat interface and visible 422/503 error messages.
