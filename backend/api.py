import os
from typing import Any, Dict, List, Literal, Optional

import json
import urllib.parse
import requests
from fastapi import FastAPI
from fastapi import HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from backend.config import get_env
from backend.executor import execute_workflow_from_graph, stream_workflow_from_graph
from backend.models import GraphState
from backend.sample import get_sample_email
from backend.services import gmail as gmail_service
from backend.services.quickbooks import exchange_authorization_code, get_qb_client


class FrontendNode(BaseModel):
    id: str
    position: Optional[Dict[str, float]] = None


class FrontendEdge(BaseModel):
    source: str
    target: str
    label: Optional[str] = None


class RunWorkflowRequest(BaseModel):
    scenario: Literal["bill", "invoice", "no_action"] = Field(
        default="bill",
        description="When classification_mode is scenario, this action is used instead of the LLM.",
    )
    classification_mode: Literal["llm", "scenario"] = Field(
        default="llm",
        description="llm: classify with LLM. scenario: use scenario field (simulate path).",
    )
    email: Optional[Dict[str, Any]] = None
    emailSource: Literal["sample", "gmail_latest"] = Field(
        default="sample",
        description="gmail_latest: newest INBOX message via Gmail API (requires OAuth).",
    )
    nodes: List[FrontendNode] = Field(default_factory=list)
    edges: List[FrontendEdge] = Field(default_factory=list)
    entryNodeId: Optional[str] = None


class OAuthCallbackExchangeRequest(BaseModel):
    code: str
    realmId: Optional[str] = None
    redirectUri: Optional[str] = None


class GmailOAuthCallbackExchangeRequest(BaseModel):
    code: str
    redirectUri: Optional[str] = None


def _resolve_workflow_email(payload: RunWorkflowRequest) -> Dict[str, Any]:
    if payload.email is not None:
        return payload.email
    if payload.emailSource == "sample":
        return get_sample_email()

    if not gmail_service.gmail_oauth_configured():
        raise HTTPException(
            status_code=400,
            detail={
                "code": "GMAIL_NOT_CONFIGURED",
                "message": (
                    "Gmail OAuth is not configured. Set GMAIL_CLIENT_ID, GMAIL_CLIENT_SECRET, "
                    "and GMAIL_REDIRECT_URI in the backend environment."
                ),
            },
        )

    if not gmail_service.gmail_tokens_present():
        try:
            authorize_url = gmail_service.build_gmail_authorize_url()
        except ValueError as exc:
            raise HTTPException(
                status_code=400,
                detail={"code": "GMAIL_NOT_CONFIGURED", "message": str(exc)},
            ) from exc
        raise HTTPException(
            status_code=401,
            detail={
                "code": "GMAIL_NOT_CONNECTED",
                "message": "Gmail is not connected. Complete the OAuth flow to grant inbox read access.",
                "authorizeUrl": authorize_url,
            },
        )

    try:
        return gmail_service.fetch_latest_inbox_message_as_email()
    except ValueError as exc:
        raise HTTPException(
            status_code=404,
            detail={"code": "GMAIL_NO_MESSAGES", "message": str(exc)},
        ) from exc
    except requests.HTTPError as exc:
        status = exc.response.status_code if exc.response is not None else 502
        raise HTTPException(
            status_code=status,
            detail={"code": "GMAIL_API_ERROR", "message": str(exc)},
        ) from exc


app = FastAPI(title="Numina Reconcile Backend API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_origin_regex=r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health() -> Dict[str, str]:
    return {"status": "ok"}


def _build_quickbooks_authorize_url() -> str:
    # Supports override for custom auth routing page
    custom_url = get_env("QB_OAUTH_AUTHORIZE_URL", "")
    if custom_url:
        return custom_url

    client_id = get_env("QB_CLIENT_ID")
    redirect_uri = get_env("QB_REDIRECT_URI")
    scope = get_env("QB_SCOPE", "com.intuit.quickbooks.accounting")
    state = get_env("QB_OAUTH_STATE", "numina-reconnect")
    params = urllib.parse.urlencode(
        {
            "client_id": client_id,
            "response_type": "code",
            "scope": scope,
            "redirect_uri": redirect_uri,
            "state": state,
        }
    )
    return f"https://appcenter.intuit.com/connect/oauth2?{params}"


def _is_token_auth_failure(status_code: int, detail: str) -> bool:
    if status_code == 401:
        return True
    lowered = detail.lower()
    return status_code == 400 and (
        "oauth.platform.intuit.com/oauth2/v1/tokens/bearer" in lowered
        or "invalid_grant" in lowered
        or "invalid_request" in lowered
        or "authenticationfailed" in lowered
        or "token expired" in lowered
    )


@app.get("/oauth/quickbooks/authorize-url")
def quickbooks_authorize_url() -> Dict[str, str]:
    return {"authorizeUrl": _build_quickbooks_authorize_url()}


@app.post("/oauth/quickbooks/callback-exchange")
def quickbooks_callback_exchange(payload: OAuthCallbackExchangeRequest) -> Dict[str, Any]:
    redirect_uri = payload.redirectUri or get_env("QB_REDIRECT_URI")
    try:
        token_data = exchange_authorization_code(payload.code, redirect_uri)
    except requests.HTTPError as exc:
        status_code = exc.response.status_code if exc.response is not None else 502
        detail = exc.response.text if exc.response is not None else str(exc)
        raise HTTPException(status_code=status_code, detail={"message": detail, "code": "QB_OAUTH_EXCHANGE_FAILED"}) from exc

    if payload.realmId:
        # Persist realm id dynamically for multi-company callbacks.
        import os
        from pathlib import Path

        os.environ["QB_REALM_ID"] = payload.realmId
        env_path = Path(".env")
        if env_path.exists():
            lines = env_path.read_text().splitlines()
            output: List[str] = []
            replaced = False
            for line in lines:
                s = line.strip()
                if s.startswith("QB_REALM_ID="):
                    output.append(f"QB_REALM_ID={payload.realmId}")
                    replaced = True
                else:
                    output.append(line)
            if not replaced:
                output.append(f"QB_REALM_ID={payload.realmId}")
            env_path.write_text("\n".join(output) + "\n")

    return {
        "ok": True,
        "realmId": payload.realmId,
        "accessTokenPresent": bool(token_data.get("access_token")),
        "refreshTokenPresent": bool(token_data.get("refresh_token")),
    }


@app.get("/oauth/gmail/status")
def gmail_oauth_status() -> Dict[str, Any]:
    urls = gmail_service.gmail_public_oauth_urls()
    return {
        "oauthConfigured": gmail_service.gmail_oauth_configured(),
        "tokensPresent": gmail_service.gmail_tokens_present(),
        "redirectUri": urls["redirectUri"],
        "javascriptOrigin": urls["javascriptOrigin"],
    }


@app.get("/oauth/gmail/authorize-url")
def gmail_authorize_url(login_hint: Optional[str] = None) -> Dict[str, str]:
    try:
        return {
            "authorizeUrl": gmail_service.build_gmail_authorize_url(
                login_hint=(login_hint or "").strip(),
            )
        }
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail={"code": "GMAIL_NOT_CONFIGURED", "message": str(exc)},
        ) from exc


@app.post("/oauth/gmail/callback-exchange")
def gmail_callback_exchange(payload: GmailOAuthCallbackExchangeRequest) -> Dict[str, Any]:
    redirect_uri = (payload.redirectUri or os.getenv("GMAIL_REDIRECT_URI") or "").strip()
    if not redirect_uri:
        raise HTTPException(
            status_code=400,
            detail={"code": "GMAIL_NOT_CONFIGURED", "message": "GMAIL_REDIRECT_URI is not set."},
        )
    try:
        token_data = gmail_service.exchange_gmail_authorization_code(payload.code, redirect_uri)
    except requests.HTTPError as exc:
        status_code = exc.response.status_code if exc.response is not None else 502
        detail = exc.response.text if exc.response is not None else str(exc)
        raise HTTPException(
            status_code=status_code,
            detail={"message": detail, "code": "GMAIL_OAUTH_EXCHANGE_FAILED"},
        ) from exc

    return {
        "ok": True,
        "accessTokenPresent": bool(token_data.get("access_token")),
        "refreshTokenPresent": bool(token_data.get("refresh_token")),
    }


@app.post("/oauth/gmail/enable")
def enable_gmail_oauth() -> Dict[str, Any]:
    if not gmail_service.gmail_oauth_configured():
        raise HTTPException(
            status_code=400,
            detail={
                "code": "GMAIL_NOT_CONFIGURED",
                "message": "Set GMAIL_CLIENT_ID, GMAIL_CLIENT_SECRET, and GMAIL_REDIRECT_URI in .env.",
            },
        )
    if not gmail_service.gmail_tokens_present():
        try:
            authorize_url = gmail_service.build_gmail_authorize_url()
        except ValueError as exc:
            raise HTTPException(
                status_code=400,
                detail={"code": "GMAIL_NOT_CONFIGURED", "message": str(exc)},
            ) from exc
        raise HTTPException(
            status_code=401,
            detail={
                "code": "GMAIL_NOT_CONNECTED",
                "message": "Gmail tokens missing. Authorize read-only inbox access.",
                "authorizeUrl": authorize_url,
            },
        )
    try:
        profile = gmail_service.validate_gmail_auth()
    except requests.HTTPError as exc:
        status_code = exc.response.status_code if exc.response is not None else 502
        detail = str(exc)
        if status_code == 401:
            try:
                authorize_url = gmail_service.build_gmail_authorize_url()
            except ValueError:
                authorize_url = ""
            raise HTTPException(
                status_code=401,
                detail={
                    "message": detail,
                    "code": "GMAIL_TOKEN_EXPIRED",
                    "authorizeUrl": authorize_url,
                },
            ) from exc
        raise HTTPException(status_code=status_code, detail={"message": detail}) from exc

    return {"enabled": True, "emailAddress": profile.get("emailAddress", "")}


@app.post("/oauth/quickbooks/enable")
def enable_quickbooks_oauth() -> Dict[str, Any]:
    required_nodes = [
        "fetch_bill_context",
        "fetch_invoice_context",
        "fetch_existing_bills",
        "fetch_existing_invoices",
        "create_bill",
        "create_invoice",
    ]
    try:
        qb = get_qb_client()
        qb.validate_auth()
    except requests.HTTPError as exc:
        status_code = exc.response.status_code if exc.response is not None else 502
        detail = str(exc)
        if _is_token_auth_failure(status_code, detail):
            raise HTTPException(
                status_code=401,
                detail={
                    "message": (
                        f"{detail}. QuickBooks token is not authorized. "
                        "Refresh QB_ACCESS_TOKEN and retry OAuth enable."
                    ),
                    "code": "QB_TOKEN_EXPIRED",
                    "authorizeUrl": _build_quickbooks_authorize_url(),
                },
            ) from exc
        raise HTTPException(status_code=status_code, detail={"message": detail}) from exc

    return {"enabled": True, "requiredNodeIds": required_nodes}


@app.post("/run-workflow")
def run_workflow(payload: RunWorkflowRequest) -> Dict[str, Any]:
    email_payload = _resolve_workflow_email(payload)

    state: GraphState = {
        "email": email_payload,
        "classification_mode": payload.classification_mode,
        "forced_scenario": payload.scenario if payload.classification_mode == "scenario" else "",
    }
    try:
        final_state, execution_order, node_logs = execute_workflow_from_graph(
            nodes=[node.model_dump() for node in payload.nodes],
            edges=[edge.model_dump() for edge in payload.edges],
            initial_state=state,
            entry_node_id=payload.entryNodeId,
        )
    except requests.HTTPError as exc:
        status_code = exc.response.status_code if exc.response is not None else 502
        detail = str(exc)
        if _is_token_auth_failure(status_code, detail):
            raise HTTPException(
                status_code=401,
                detail={
                    "message": (
                        f"{detail}. QuickBooks access token is likely expired/invalid. "
                        "Refresh QB_ACCESS_TOKEN and retry."
                    ),
                    "code": "QB_TOKEN_EXPIRED",
                    "authorizeUrl": _build_quickbooks_authorize_url(),
                },
            ) from exc
        raise HTTPException(status_code=status_code, detail={"message": detail}) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail={"message": f"Workflow execution failed: {exc}"}) from exc

    return {
        "result": final_state.get("result", {"action": "no_action", "reason": "empty_result"}),
        "executionOrder": execution_order,
        "nodeLogs": node_logs,
    }


@app.post("/run-workflow/stream")
def run_workflow_stream(payload: RunWorkflowRequest) -> StreamingResponse:
    email_payload = _resolve_workflow_email(payload)
    state: GraphState = {
        "email": email_payload,
        "classification_mode": payload.classification_mode,
        "forced_scenario": payload.scenario if payload.classification_mode == "scenario" else "",
    }

    def event_stream():
        try:
            for event in stream_workflow_from_graph(
                nodes=[node.model_dump() for node in payload.nodes],
                edges=[edge.model_dump() for edge in payload.edges],
                initial_state=state,
                entry_node_id=payload.entryNodeId,
            ):
                yield json.dumps(event) + "\n"
        except requests.HTTPError as exc:
            status_code = exc.response.status_code if exc.response is not None else 502
            detail = str(exc)
            if _is_token_auth_failure(status_code, detail):
                yield json.dumps(
                    {
                        "event": "workflow_error",
                        "statusCode": 401,
                        "code": "QB_TOKEN_EXPIRED",
                        "message": (
                            f"{detail}. QuickBooks access token is likely expired/invalid. "
                            "Refresh QB_ACCESS_TOKEN and retry."
                        ),
                        "authorizeUrl": _build_quickbooks_authorize_url(),
                    }
                ) + "\n"
                return
            yield json.dumps({"event": "workflow_error", "statusCode": status_code, "message": detail}) + "\n"
        except Exception as exc:
            yield json.dumps({"event": "workflow_error", "statusCode": 500, "message": str(exc)}) + "\n"

    return StreamingResponse(event_stream(), media_type="application/x-ndjson")

