# hermes-web-console

Unofficial web console for [Hermes Agent](https://github.com/NousResearch/hermes-agent) — streaming chat UI with tool-call visualization + model config management, bridged by a FastAPI backend that talks only to the dashboard API.

> Community project, not affiliated with Nous Research. Requires a running Hermes Agent dashboard.

## Architecture

```
Browser (React + Vite)
   │  /api/*  (your own protocol, SSE for chat)
   ▼
FastAPI backend (holds Hermes credentials in env)
   │  dashboard REST + WS JSON-RPC
   ▼
Hermes Agent dashboard (9119)
```

The browser never talks to Hermes directly — credentials live only in the backend's environment.

## Features

- Streaming chat (`session.create` → `prompt.submit` → `message.delta` events over the dashboard's JSON-RPC WebSocket)
- Model list (custom endpoints only) with search
- Model config management (CRUD + validate + set-default) — backend endpoints ready

## Quick start

```bash
# 1. backend
cd backend
pip install -r requirements.txt
cp .env.example .env   # fill in HERMES_PASS etc.
python -m uvicorn main:app --port 8000

# 2. frontend
cd ../frontend
npm install
npm run dev            # http://localhost:5173
```

## Security notes

- All credentials go through environment variables — see `.env.example`.
- The backend is unauthenticated by default (`APP_AUTH` unset); bind it to localhost and don't expose it publicly as-is.

## License

MIT
