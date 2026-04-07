# Numina Reconcile Agent

LangGraph workflow that ingests email-like payloads, classifies them as **bill**, **invoice**, or **no_action**, fetches QuickBooks context, checks for duplicates, and creates a **Bill** or **Invoice** in QuickBooks Online (sandbox). Optional **Gmail OAuth** loads the latest **INBOX** message instead of the built-in sample.

## Repository layout

| Path | Purpose |
|------|---------|
| `backend/` | LangGraph nodes, QuickBooks + Gmail services, FastAPI API |
| `main.py` | CLI: runs sample email through `run_once()` |
| `Accounting-Orchestrator/` | React + Vite + React Flow UI (live streaming runs, OAuth sidebars); **nested git repo** — if the folder is empty after clone, run `git submodule update --init --recursive` (or `cd Accounting-Orchestrator && git fetch && git checkout` the commit shown by `git ls-tree HEAD Accounting-Orchestrator` in the parent repo). |
| `.env.example` | Template for environment variables (copy to `.env`) |

## 1) Python backend

### Install

```bash
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### Environment (`.env`)

Create `.env` in the **project root** (same folder as `backend/` and `main.py`). Copy from `.env.example`. **Do not commit `.env`.**

`backend/config.py` loads `.env` from the **repository root** (not only the shell’s current directory), so `uvicorn` picks up variables even if you start it from another folder.

| Variable | Purpose |
|----------|---------|
| `QB_REALM_ID` | QuickBooks company realm ID |
| `QB_ACCESS_TOKEN` / `QB_REFRESH_TOKEN` | OAuth tokens (refresh supported) |
| `QB_CLIENT_ID` / `QB_CLIENT_SECRET` | Intuit app credentials |
| `QB_REDIRECT_URI` | Must match Intuit app (e.g. `http://localhost:5173/callback`) |
| `QB_MINOR_VERSION` | API minor version (default `69`) |
| `QB_DEFAULT_EXPENSE_ACCOUNT_ID` | Optional: default account id for bill lines missing `AccountRef` |
| `QB_DEFAULT_INVOICE_ITEM_ID` | Optional: preferred QuickBooks **Item** id for invoice lines built from the email body (must not be a Category-type item) |
| `LLM_PROVIDER` | `ollama` (default) or `gemini` |
| `OLLAMA_MODEL` | e.g. `qwen2.5:7b` (when using Ollama) |
| `OLLAMA_BASE_URL` | Optional; set if Ollama is not on `localhost:11434` |
| `LLM_MAX_PAYLOAD_CHARS` | Optional (Ollama): max JSON size for the LLM user message (default `48000`). Truncation **prefers keeping all items and vendors** and shortens **accounts** first, then items, then vendors only if needed |
| `GOOGLE_API_KEY` | Required if `LLM_PROVIDER=gemini` |
| `GEMINI_MODEL` | e.g. `gemini-2.5-flash` (when using Gemini) |
| `TRACK_EMAIL` | If **empty**, all emails pass the inspect filter. If **unset**, code defaults to `numinatest2@gmail.com` — set `TRACK_EMAIL=` explicitly to allow any sender for real Gmail. |
| `GMAIL_CLIENT_ID` / `GMAIL_CLIENT_SECRET` | Google OAuth client |
| `GMAIL_REDIRECT_URI` | Full callback URL (e.g. `http://localhost:5173/gmail-callback`) — must match Google Console **exactly** |
| `GMAIL_ACCESS_TOKEN` / `GMAIL_REFRESH_TOKEN` | Written by backend after Gmail OAuth (if `.env` exists) |

### Gmail OAuth

1. [Google Cloud Console](https://console.cloud.google.com/): enable **Gmail API**, create **OAuth 2.0 (Web)** credentials.
2. **Authorized JavaScript origins**: `http://localhost:<vite-port>` (no path).
3. **Authorized redirect URIs**: `http://localhost:<vite-port>/gmail-callback` — must match `GMAIL_REDIRECT_URI` **character-for-character** (scheme, host, port, path).
4. **Google Auth Platform → Data Access**: add scope `https://www.googleapis.com/auth/gmail.readonly` (or use “Add or remove scopes”).
5. **OAuth consent screen**: while app is **Testing**, add every user under **Test users** or they get `403 access_denied`.
6. Put `GMAIL_CLIENT_ID`, `GMAIL_CLIENT_SECRET`, `GMAIL_REDIRECT_URI` in `.env`, restart `uvicorn`.

**UI:** Sidebar shows **Google Cloud (exact strings)** from `GET /oauth/gmail/status` (`redirectUri`, `javascriptOrigin`) and warns if the browser origin does not match `.env` (common cause of `redirect_uri_mismatch`).

**Flow:** **Enable Gmail OAuth** → Google account picker (`select_account`) → `/gmail-callback` exchanges code → tokens saved. With Gmail tokens, **Run Workflow** sends `emailSource: gmail_latest` (newest INBOX message).

**API:** `GET /oauth/gmail/status`, `GET /oauth/gmail/authorize-url` (optional `login_hint`), `POST /oauth/gmail/callback-exchange`, `POST /oauth/gmail/enable`.

### API: run workflow

`POST /run-workflow` and `POST /run-workflow/stream` accept:

| Field | Notes |
|-------|--------|
| `emailSource` | `sample` (default) or `gmail_latest` (requires Gmail OAuth tokens) |
| `classification_mode` | `llm` (default): LLM classifies. `scenario`: skip LLM and use `scenario` |
| `scenario` | `bill` \| `invoice` \| `no_action` — used when `classification_mode` is `scenario` |
| `nodes` / `edges` | React Flow graph from the UI |

### Run CLI (sample email only)

```bash
python main.py
```

### Run API server

```bash
uvicorn backend.api:app --reload --port 8000
```

**Endpoints (summary):** `GET /health`, QuickBooks `GET/POST /oauth/quickbooks/*`, Gmail `GET/POST /oauth/gmail/*`, `POST /run-workflow`, `POST /run-workflow/stream` (NDJSON). Non-stream responses include `workflowFailed` / `workflowFailureReason` when a node raises or the run hits `max_steps`.

## 2) Frontend (`Accounting-Orchestrator`)

```bash
cd Accounting-Orchestrator
npm install
npm run dev
```

- **QuickBooks:** `/callback` exchanges code with backend; **Enable QuickBooks OAuth** in sidebar.
- **Gmail:** `/gmail-callback` for Google return; **Enable Gmail OAuth** in sidebar.
- **Toolbar:** **Auto — LLM classify** (default) or **Simulate bill / invoice / no-action path** (forces branch without LLM; sent as `classification_mode` / `scenario`).
- Backend URL is `http://localhost:8000` (hardcoded in the client).

Match **ports** in Intuit app, Google Console, `GMAIL_REDIRECT_URI`, and the URL you open in the browser.

## 3) Workflow behavior (high level)

1. **Inspect email** — optional filter via `TRACK_EMAIL`; with Gmail, payload is latest INBOX message.
2. **Classify** — if the plain-text body clearly looks like a **vendor bill** or **customer invoice**, that wins without calling the LLM; otherwise LLM (or **forced scenario** from the toolbar).
3. **Branch**
   - **Bill:** fetch vendors, items, accounts → **parse bill** (LLM + merge) → existing bills → duplicate check → create bill.
   - **Invoice:** fetch customers, items → **parse invoice** (LLM + merge) → existing invoices → duplicate check → create invoice.

**Parse merge (after the LLM):** vendor/customer refs and dates are reconciled from the email body when missing; labeled **Subtotal / Tax / Total** lines fill `duplicate_check` when the model omitted them. If the model returns **no line items** but the body lists amounts, lines are built from the body (QuickBooks **items** when possible, else expense **accounts**). Body parsing skips header/summary lines, so when **subtotal + tax = total** is present in the body, a **tax line** may be appended automatically for that fallback path.

### Bill / invoice extraction (tax and totals)

Parse prompts instruct the LLM to:

- Add a **separate bill line** (`AccountBasedExpenseLineDetail`) for **tax / VAT / GST** when the email states a tax **dollar amount**, with `TaxCodeRef` `NON`, so line sums match **Subtotal + Tax = Total** when those appear in the email.
- Put **subtotal**, **tax**, and **total** in `duplicate_check` when present for reconciliation.

## 4) Security notes

- Never commit `.env` or live tokens.
- Rotate credentials if exposed (including in chat or screenshots).
- QuickBooks: refresh via `QB_REFRESH_TOKEN`. Gmail: tokens persisted to `.env` when the file exists at project root.

## 5) Mapping from original n8n idea

- Gmail → `email` payload (sample, API, or `gmail_latest`).
- QuickBooks → `QuickBooksClient` + graph nodes.
- LLM → `classify_email`, `parse_bill`, `parse_invoice`.
