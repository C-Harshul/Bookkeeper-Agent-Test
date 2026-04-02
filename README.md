# Numina Reconcile Agent

LangGraph workflow that ingests email-like payloads, classifies them as **bill**, **invoice**, or **no_action**, fetches QuickBooks context, checks for duplicates, and creates a **Bill** or **Invoice** in QuickBooks Online (sandbox).

## Repository layout

| Path | Purpose |
|------|---------|
| `backend/` | LangGraph nodes, QuickBooks client, token refresh, FastAPI API |
| `main.py` | CLI entry: runs sample email through `run_once()` |
| `Accounting-Orchestrator/` | React + Vite + React Flow UI (workflow canvas, live execution) |

## 1) Python backend

### Install

```bash
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### Environment (`.env`)

Create `.env` in the project root (see `python-dotenv` loading in `backend/config.py`). Do not commit secrets.

| Variable | Purpose |
|----------|---------|
| `QB_REALM_ID` | QuickBooks company realm ID |
| `QB_ACCESS_TOKEN` | Short-lived OAuth access token |
| `QB_REFRESH_TOKEN` | Long-lived refresh token (auto-refresh) |
| `QB_CLIENT_ID` / `QB_CLIENT_SECRET` | Intuit app credentials |
| `QB_REDIRECT_URI` | Must match Intuit app redirect (e.g. `http://localhost:5174/callback`) |
| `QB_MINOR_VERSION` | API minor version (default `69`) |
| `LLM_PROVIDER` | `gemini` or `ollama` |
| `GOOGLE_API_KEY` | Required if `LLM_PROVIDER=gemini` |
| `GEMINI_MODEL` | e.g. `gemini-2.5-flash` |
| `TRACK_EMAIL` | Optional filter for which emails to process |

### Run CLI (sample email)

```bash
python main.py
```

### Run workflow API (for UI)

```bash
uvicorn backend.api:app --reload --port 8000
```

Useful endpoints:

- `GET /health` — health check
- `GET /oauth/quickbooks/authorize-url` — Intuit OAuth URL
- `POST /oauth/quickbooks/callback-exchange` — exchange `code` + persist tokens
- `POST /oauth/quickbooks/enable` — validate QuickBooks access
- `POST /run-workflow/stream` — NDJSON stream of node events (used by the UI)

## 2) Frontend (`Accounting-Orchestrator`)

```bash
cd Accounting-Orchestrator
npm install
npm run dev
```

Default Vite port may be `5173`; your Intuit `QB_REDIRECT_URI` must match the URL you actually serve (e.g. port `5174` if you registered `/callback` there).

The UI calls the backend at `http://localhost:8000` for **Run Workflow** (streaming) and OAuth.

## 3) Workflow behavior (high level)

1. **Inspect email** — filter by `TRACK_EMAIL` (optional)
2. **Classify** — LLM: `bill` / `invoice` / `no_action`
3. **Branch**
   - Bill: fetch vendors, items, accounts → parse bill → fetch existing bills → duplicate check → create bill
   - Invoice: fetch customers, items → parse invoice → fetch existing invoices → duplicate check → create invoice

## 4) Security notes

- Never commit `.env` or live tokens.
- Rotate `GOOGLE_API_KEY` and QuickBooks tokens if they were exposed.
- QuickBooks access tokens expire; refresh via `QB_REFRESH_TOKEN` + client credentials.

## 5) Mapping from original n8n idea

- Gmail trigger → pass `email` dict into API or `run_once`
- QuickBooks tools → `backend` nodes + `QuickBooksClient`
- LLM steps → `classify_email`, `parse_bill`, `parse_invoice`
