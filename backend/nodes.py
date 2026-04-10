import os
from typing import Any, Dict, Optional

import requests
from langchain_core.output_parsers import PydanticOutputParser

from backend.models import (
    BillAgentOutput,
    ClassificationOutput,
    GraphState,
    InvoiceAgentOutput,
)
from backend.services.llm import parse_structured_output
from backend.services.quickbooks import get_qb_client
from backend.utils import (
    body_has_strong_classification_signal,
    build_bill_llm_reference,
    build_bill_payload_from_email,
    build_email_blob,
    build_invoice_llm_reference,
    build_invoice_payload_from_email,
    clamp_bill_agent_output_to_quickbooks,
    detect_bill_duplicate,
    detect_invoice_duplicate,
    get_email_body_text,
    log_step,
    quick_classify_email_body,
    sanitize_bill_payload,
    should_track_email,
    summarize_counts,
)


def _response_body_from_http_error(response: Optional[requests.Response]) -> Any:
    if response is None:
        return None
    try:
        return response.json()
    except ValueError:
        text = (response.text or "").strip()
        return text[:8000] if text else None


def _quickbooks_post_attempt(
    *,
    resource: str,
    request_body: Dict[str, Any],
    http_status: int,
    response_body: Any,
) -> Dict[str, Any]:
    return {
        "resource": resource,
        "method": "POST",
        "requestBody": request_body,
        "httpStatus": http_status,
        "responseBody": response_body,
    }


def inspect_email_node(state: GraphState) -> GraphState:
    log_step("inspect_email", "Reading incoming email payload")
    email = state.get("email", {})
    body_text = (email.get("text") or "").strip()
    body_html = (email.get("html") or "").strip()
    print("\n=== Incoming Email ===")
    print(f"From: {email.get('from', '')}")
    print(f"Subject: {email.get('subject', '')}")
    print(f"Date: {email.get('date', '')}")
    print("Body:")
    print(body_text if body_text else body_html if body_html else "<empty>")
    print("======================\n")

    target_email = os.getenv("TRACK_EMAIL", "numinatest2@gmail.com").strip().lower()
    log_step("inspect_email", f"Tracking target email: {target_email}")
    if not should_track_email(email, target_email):
        log_step("inspect_email", "Email did not match tracking filter. Stopping workflow.")
        return {
            **state,
            "result": {
                "action": "no_action",
                "reason": f"email_not_tracked:{target_email}",
            },
        }
    log_step("inspect_email", "Email matched tracking filter. Continuing workflow.")
    return state


def classify_email_node(state: GraphState) -> GraphState:
    forced = (state.get("forced_scenario") or "").strip().lower()
    if state.get("classification_mode") == "scenario" and forced in ("bill", "invoice", "no_action"):
        log_step("classify_email", f"Using forced scenario (no LLM): action={forced}")
        return {**state, "action": forced, "rationale": "forced_scenario"}

    body_text = get_email_body_text(state["email"])
    quick = quick_classify_email_body(body_text)
    if quick:
        action, rationale = quick
        log_step("classify_email", f"Quick body classify: action={action}")
        return {**state, "action": action, "rationale": rationale}

    log_step("classify_email", "Classifying incoming email as bill/invoice/no_action (LLM)")
    parser = PydanticOutputParser(pydantic_object=ClassificationOutput)
    email_blob = build_email_blob(state["email"])
    system_prompt = (
        "You classify accounting emails.\n"
        "Return action in {'bill','invoice','no_action'} only.\n"
        "Use no_action for unrelated, ambiguous, or weak-signal emails.\n"
        "Prefer 'bill' for amounts owed to suppliers/vendors (accounts payable). "
        "Prefer 'invoice' for sales/customer-facing invoices (accounts receivable).\n"
        "If evidence is weak (e.g. generic mention of invoice/bill without document fields like "
        "invoice number/date, due date, subtotal/tax/total), choose no_action.\n"
        f"{parser.get_format_instructions()}"
    )
    try:
        classified = parse_structured_output(
            parser=parser,
            system_prompt=system_prompt,
            user_payload={"email": email_blob, "email_body_text": body_text},
        )
    except Exception as exc:
        log_step("classify_email", f"Classification failed: {exc}")
        return {
            **state,
            "action": "no_action",
            "rationale": f"classification_error:{exc}",
        }
    action = classified.action
    rationale = classified.rationale or ""
    if action in ("bill", "invoice") and not body_has_strong_classification_signal(body_text):
        log_step("classify_email", f"Weak evidence for action={action}; overriding to no_action")
        action = "no_action"
        rationale = f"{rationale} weak_signal_override".strip()
    log_step("classify_email", f"Classified action={action}")
    return {
        **state,
        "action": action,
        "rationale": rationale,
    }


def fetch_bill_context_node(state: GraphState) -> GraphState:
    log_step("fetch_bill_context", "Fetching vendors, items, accounts for bill processing")
    qb = get_qb_client()
    next_state = {
        **state,
        "items": qb.get_items(),
        "vendors": qb.get_vendors(),
        "accounts": qb.get_accounts(),
        "tax_codes": qb.get_tax_codes(),
    }
    summarize_counts(
        "fetch_bill_context",
        {
            "items": next_state["items"],
            "vendors": next_state["vendors"],
            "accounts": next_state["accounts"],
            "tax_codes": next_state["tax_codes"],
        },
    )
    return next_state


def fetch_invoice_context_node(state: GraphState) -> GraphState:
    log_step("fetch_invoice_context", "Fetching customers and items for invoice processing")
    qb = get_qb_client()
    next_state = {
        **state,
        "items": qb.get_items(),
        "customers": qb.get_customers(),
    }
    summarize_counts("fetch_invoice_context", {"items": next_state["items"], "customers": next_state["customers"]})
    return next_state


def parse_bill_node(state: GraphState) -> GraphState:
    log_step("parse_bill", "Running LLM bill extraction")
    parser = PydanticOutputParser(pydantic_object=BillAgentOutput)
    email_blob = build_email_blob(state["email"])
    email_body_text = get_email_body_text(state["email"])

    system_prompt = (
        "You are an Accounting AI Agent for BILL extraction only.\n"
        "GROUNDING (non-negotiable): Only use QuickBooks data from reference.* lists returned by the API. "
        "Every VendorRef.value, ItemRef.value, AccountRef.value, and APAccountRef.value MUST be copied "
        "exactly from an Id in reference.vendors, reference.items (sellable rows), or reference.accounts. "
        "Never invent, guess, or reuse Ids not shown in reference. "
        "Line Amounts, subtotals, tax, and totals MUST match figures stated in email_body_text or email when present; "
        "do not fabricate dollar amounts.\n"
        "Rules:\n"
        "1) Return only Bill fields in the schema.\n"
        "2) For bill line details: if matching item has expense account use "
        "ItemBasedExpenseLineDetail, otherwise use AccountBasedExpenseLineDetail.\n"
        "3) Use VendorRef only from reference.vendors Ids. If no vendor matches the email, omit VendorRef.\n"
        "3b) reference.vendors and reference.items are the QuickBooks lists — pick Ids only from them.\n"
        "4) Use email_body_text for line items and amounts when the structured email fields are sparse.\n"
        "5) Dates: Set TxnDate to the vendor bill / invoice date from the email (e.g. 'Bill Date:', 'Invoice Date:'). "
        "Set DueDate to the payment due date (e.g. 'Due Date:'). Use yyyy-mm-dd when possible; otherwise the pipeline "
        "will normalize common formats.\n"
        "6) Tax, VAT, GST, sales tax: NEVER add a separate expense Line for tax. QuickBooks applies tax via TaxCodeRef "
        "on each line and company tax settings. Put only pre-tax product/service Lines; their Amounts must sum to the "
        "email Subtotal when Subtotal is shown. Put the stated tax dollar amount in duplicate_check.tax only. "
        "If the email only shows a Tax line without Subtotal, estimate subtotal = total - tax for duplicate_check.subtotal "
        "and keep Line amounts consistent with that subtotal.\n"
        "7) Shipping or other fees: If the email lists separate dollar amounts, add "
        "AccountBasedExpenseLineDetail lines with matching Amounts.\n"
        "8) duplicate_check must include when present: subtotal, tax, total (numbers) from the email. "
        "Subtotal + tax should equal total (within $0.02) when all three appear.\n"
        "9) Include other duplicate_check hints helpful for comparison.\n"
        f"{parser.get_format_instructions()}"
    )

    reference = build_bill_llm_reference(
        state.get("items", []),
        state.get("vendors", []),
        state.get("accounts", []),
    )
    user_prompt = {
        "email": email_blob,
        "email_body_text": email_body_text,
        "reference": reference,
    }

    parsed = parse_structured_output(parser=parser, system_prompt=system_prompt, user_payload=user_prompt)
    grounded = clamp_bill_agent_output_to_quickbooks(
        parsed.model_dump(),
        items=state.get("items", []),
        vendors=state.get("vendors", []),
        accounts=state.get("accounts", []),
    )
    merged = build_bill_payload_from_email(
        state["email"],
        grounded,
        items=state.get("items", []),
        vendors=state.get("vendors", []),
        accounts=state.get("accounts", []),
        tax_codes=state.get("tax_codes", []),
    )
    log_step("parse_bill", "Bill payload parsed")
    return {**state, "parsed_bill": merged}


def parse_invoice_node(state: GraphState) -> GraphState:
    log_step("parse_invoice", "Running LLM invoice extraction")
    parser = PydanticOutputParser(pydantic_object=InvoiceAgentOutput)
    email_blob = build_email_blob(state["email"])
    email_body_text = get_email_body_text(state["email"])

    system_prompt = (
        "You are an Accounting AI Agent for INVOICE extraction only.\n"
        "Rules:\n"
        "1) Return only Invoice fields in the schema.\n"
        "2) Use CustomerRef from provided customers.\n"
        "3) Use SalesItemLineDetail.ItemRef from provided items.\n"
        "3b) reference.customers and reference.items list every customer and item from QuickBooks; use only those Ids.\n"
        "4) Use email_body_text for line items and amounts when the structured email fields are sparse.\n"
        "4b) Ignore Open Balance, Previous Balance, Balance forward, aging/statement balance, and any "
        "AR history — never add those as Line items; only charges for this invoice.\n"
        "5) Tax / VAT / GST: If the email shows tax as a separate amount, add a Line: "
        "use a reference item that represents tax/fees if one exists; otherwise pick the "
        "closest generic item and set Description to the tax label from the email "
        "(e.g. 'Sales tax 10%') and Amount to the tax dollar amount so Line amounts "
        "sum to the invoice Total. If line prices are tax-inclusive, do not add a "
        "separate tax line; note in rationale.\n"
        "6) Subtotal, tax, total: duplicate_check must include subtotal, tax, total "
        "when the email states them; sum(Line.Amount) should match stated Total.\n"
        "7) Include other duplicate_check hints helpful for comparison.\n"
        f"{parser.get_format_instructions()}"
    )

    reference = build_invoice_llm_reference(
        state.get("items", []),
        state.get("customers", []),
    )
    user_prompt = {
        "email": email_blob,
        "email_body_text": email_body_text,
        "reference": reference,
    }
    parsed = parse_structured_output(parser=parser, system_prompt=system_prompt, user_payload=user_prompt)
    merged = build_invoice_payload_from_email(
        state["email"],
        parsed.model_dump(),
        items=state.get("items", []),
        customers=state.get("customers", []),
    )
    log_step("parse_invoice", "Invoice payload parsed")
    return {**state, "parsed_invoice": merged}


def fetch_existing_bills_node(state: GraphState) -> GraphState:
    log_step("fetch_existing_bills", "Fetching existing bills for duplicate check")
    qb = get_qb_client()
    bills = qb.get_bills()
    log_step("fetch_existing_bills", f"Fetched bills={len(bills)}")
    return {**state, "bills": bills}


def fetch_existing_invoices_node(state: GraphState) -> GraphState:
    log_step("fetch_existing_invoices", "Fetching existing invoices for duplicate check")
    qb = get_qb_client()
    invoices = qb.get_invoices()
    log_step("fetch_existing_invoices", f"Fetched invoices={len(invoices)}")
    return {**state, "invoices": invoices}


def check_bill_duplicate_node(state: GraphState) -> GraphState:
    log_step("check_bill_duplicate", "Checking duplicate bill")
    bill = state.get("parsed_bill", {}).get("bill", {})
    duplicate = detect_bill_duplicate(bill, state.get("bills", []))
    log_step("check_bill_duplicate", f"Duplicate found={duplicate}")
    return {**state, "duplicate_found": duplicate}


def check_invoice_duplicate_node(state: GraphState) -> GraphState:
    log_step("check_invoice_duplicate", "Checking duplicate invoice")
    invoice = state.get("parsed_invoice", {}).get("invoice", {})
    duplicate = detect_invoice_duplicate(invoice, state.get("invoices", []))
    log_step("check_invoice_duplicate", f"Duplicate found={duplicate}")
    return {**state, "duplicate_found": duplicate}


def create_bill_node(state: GraphState) -> GraphState:
    log_step("create_bill", "Creating Bill in QuickBooks")
    qb = get_qb_client()
    payload = sanitize_bill_payload(state["parsed_bill"]["bill"], state.get("accounts", []))
    try:
        result = qb.create_bill(payload)
    except requests.HTTPError as exc:
        resp = exc.response
        status = int(resp.status_code) if resp is not None else 0
        body = _response_body_from_http_error(resp)
        post = _quickbooks_post_attempt(
            resource="bill",
            request_body=payload,
            http_status=status,
            response_body=body,
        )
        log_step("create_bill", f"Bill POST failed httpStatus={status}")
        return {
            **state,
            "result": {
                "action": "bill_create_failed",
                "post": post,
                "response": body,
                "error": str(exc),
            },
            "workflow_failed": True,
            "workflow_failure_reason": f"create_bill: {exc}",
        }
    log_step("create_bill", f"Bill created successfully. Response keys={list(result.keys())}")
    post = _quickbooks_post_attempt(
        resource="bill",
        request_body=payload,
        http_status=200,
        response_body=result,
    )
    return {
        **state,
        "result": {"action": "bill_created", "post": post, "response": result},
    }


def create_invoice_node(state: GraphState) -> GraphState:
    log_step("create_invoice", "Creating Invoice in QuickBooks")
    qb = get_qb_client()
    payload = state["parsed_invoice"]["invoice"]
    try:
        result = qb.create_invoice(payload)
    except requests.HTTPError as exc:
        resp = exc.response
        status = int(resp.status_code) if resp is not None else 0
        body = _response_body_from_http_error(resp)
        post = _quickbooks_post_attempt(
            resource="invoice",
            request_body=payload,
            http_status=status,
            response_body=body,
        )
        log_step("create_invoice", f"Invoice POST failed httpStatus={status}")
        return {
            **state,
            "result": {
                "action": "invoice_create_failed",
                "post": post,
                "response": body,
                "error": str(exc),
            },
            "workflow_failed": True,
            "workflow_failure_reason": f"create_invoice: {exc}",
        }
    log_step("create_invoice", f"Invoice created successfully. Response keys={list(result.keys())}")
    post = _quickbooks_post_attempt(
        resource="invoice",
        request_body=payload,
        http_status=200,
        response_body=result,
    )
    return {
        **state,
        "result": {"action": "invoice_created", "post": post, "response": result},
    }


def no_action_node(state: GraphState) -> GraphState:
    parsed = state.get("parsed_bill") or state.get("parsed_invoice") or {}
    reason = "duplicate" if state.get("duplicate_found") else state.get("rationale", "no_action")
    log_step("no_action", f"No action taken. Reason={reason}")
    return {**state, "result": {"action": "no_action", "reason": reason, "parsed": parsed}}

