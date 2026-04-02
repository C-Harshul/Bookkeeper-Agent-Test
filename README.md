# LangGraph Accounting Reconcile Agent

This project translates your n8n workflow into a LangGraph app that:
- reads a Gmail-like email payload
- classifies it as `bill`, `invoice`, or `no_action`
- fetches QuickBooks reference data (bills, invoices, items, customers, vendors)
- checks duplicates before write operations
- creates either a Bill or Invoice in QuickBooks

## 1) Install

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## 2) Configure environment

```bash
export QB_REALM_ID="9341455238954937"
export QB_ACCESS_TOKEN="<quickbooks_oauth_access_token>"
export QB_MINOR_VERSION="69"

# LLM selection (choose one provider)
export LLM_PROVIDER="ollama"
export OLLAMA_MODEL="qwen2.5:7b"
# Optional if Ollama is not local default:
export OLLAMA_BASE_URL="http://localhost:11434"

# Or use Gemini temporarily for testing:
# export LLM_PROVIDER="gemini"
# export GOOGLE_API_KEY="<your_google_ai_api_key>"
# export GEMINI_MODEL="gemini-2.5-flash"
```

## 3) Run

```bash
python main.py
```

## 4) Run backend API for frontend execution

```bash
uvicorn backend.api:app --reload --port 8000
```

The frontend (`Accounting-Orchestrator`) calls `POST /run-workflow` to execute the graph order defined by current UI nodes/edges.

## Mapping from n8n to LangGraph

- `Gmail Trigger` -> provide `email_payload` to `run_once(...)`
- `GetBills`, `GetInvoice`, `GetItems`, `GetCustomers1`, `GetVendors1` -> `fetch_reference_data_node`
- `AI Agent` -> `parse_email_node` (LLM + structured parser)
- `Code` + `Switch` -> `duplicate_check_node` + `route_action`
- `Create Bill` -> `create_bill_node`
- `Create Invoice` -> `create_invoice_node`

## Important

- Do **not** hardcode bearer tokens in source code.
- Use short-lived OAuth access tokens from your QuickBooks OAuth flow.
- In production, replace `run_once(sample)` with a real Gmail ingestion path (Pub/Sub, IMAP, or webhook bridge).
