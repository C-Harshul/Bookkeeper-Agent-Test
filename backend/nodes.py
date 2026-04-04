import os

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
    build_email_blob,
    detect_bill_duplicate,
    detect_invoice_duplicate,
    log_step,
    sanitize_bill_payload,
    should_track_email,
    summarize_counts,
)


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

    log_step("classify_email", "Classifying incoming email as bill/invoice/no_action (LLM)")
    parser = PydanticOutputParser(pydantic_object=ClassificationOutput)
    email_blob = build_email_blob(state["email"])
    system_prompt = (
        "You classify accounting emails.\n"
        "Return action in {'bill','invoice','no_action'} only.\n"
        "Use no_action for unrelated emails.\n"
        f"{parser.get_format_instructions()}"
    )
    classified = parse_structured_output(
        parser=parser,
        system_prompt=system_prompt,
        user_payload={"email": email_blob},
    )
    log_step("classify_email", f"Classified action={classified.action}")
    return {
        **state,
        "action": classified.action,
        "rationale": classified.rationale or "",
    }


def fetch_bill_context_node(state: GraphState) -> GraphState:
    log_step("fetch_bill_context", "Fetching vendors, items, accounts for bill processing")
    qb = get_qb_client()
    next_state = {
        **state,
        "items": qb.get_items(),
        "vendors": qb.get_vendors(),
        "accounts": qb.get_accounts(),
    }
    summarize_counts(
        "fetch_bill_context",
        {"items": next_state["items"], "vendors": next_state["vendors"], "accounts": next_state["accounts"]},
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

    system_prompt = (
        "You are an Accounting AI Agent for BILL extraction only.\n"
        "Rules:\n"
        "1) Return only Bill fields in the schema.\n"
        "2) For bill line details: if matching item has expense account use "
        "ItemBasedExpenseLineDetail, otherwise use AccountBasedExpenseLineDetail.\n"
        "3) Use VendorRef from provided vendors.\n"
        "4) Tax, VAT, GST, and sales tax: If the email shows a separate tax amount (e.g. "
        "'Tax (10%): $296', 'VAT: …', 'Sales tax …'), add a separate Line with "
        "DetailType AccountBasedExpenseLineDetail, Description matching the email "
        "(e.g. 'Sales tax (10%)'), Amount equal to that tax dollar amount, "
        "TaxCodeRef value 'NON', and AccountRef from a suitable expense or tax account "
        "in reference.accounts when possible; otherwise any plausible expense AccountRef id.\n"
        "5) Subtotal vs total: If the email gives Subtotal and Tax and Total, line items "
        "should sum to subtotal (pre-tax) and the tax line equals the stated tax; "
        "sum of all Line.Amount must equal the email Total (within $0.02). "
        "If items are clearly tax-inclusive only, omit a separate tax line and note in rationale.\n"
        "6) Shipping or other fees: If the email lists separate dollar amounts, add "
        "AccountBasedExpenseLineDetail lines with matching Amounts.\n"
        "7) duplicate_check must include when present: subtotal, tax, total (numbers) "
        "from the email for reconciliation.\n"
        "8) Include other duplicate_check hints helpful for comparison.\n"
        f"{parser.get_format_instructions()}"
    )

    user_prompt = {
        "email": email_blob,
        "reference": {
            "items": state.get("items", []),
            "vendors": state.get("vendors", []),
            "accounts": state.get("accounts", []),
        },
    }

    parsed = parse_structured_output(parser=parser, system_prompt=system_prompt, user_payload=user_prompt)
    log_step("parse_bill", "Bill payload parsed")
    return {**state, "parsed_bill": parsed.model_dump()}


def parse_invoice_node(state: GraphState) -> GraphState:
    log_step("parse_invoice", "Running LLM invoice extraction")
    parser = PydanticOutputParser(pydantic_object=InvoiceAgentOutput)
    email_blob = build_email_blob(state["email"])

    system_prompt = (
        "You are an Accounting AI Agent for INVOICE extraction only.\n"
        "Rules:\n"
        "1) Return only Invoice fields in the schema.\n"
        "2) Use CustomerRef from provided customers.\n"
        "3) Use SalesItemLineDetail.ItemRef from provided items.\n"
        "4) Tax / VAT / GST: If the email shows tax as a separate amount, add a Line: "
        "use a reference item that represents tax/fees if one exists; otherwise pick the "
        "closest generic item and set Description to the tax label from the email "
        "(e.g. 'Sales tax 10%') and Amount to the tax dollar amount so Line amounts "
        "sum to the invoice Total. If line prices are tax-inclusive, do not add a "
        "separate tax line; note in rationale.\n"
        "5) Subtotal, tax, total: duplicate_check must include subtotal, tax, total "
        "when the email states them; sum(Line.Amount) should match stated Total.\n"
        "6) Include other duplicate_check hints helpful for comparison.\n"
        f"{parser.get_format_instructions()}"
    )

    user_prompt = {
        "email": email_blob,
        "reference": {
            "items": state.get("items", []),
            "customers": state.get("customers", []),
        },
    }
    parsed = parse_structured_output(parser=parser, system_prompt=system_prompt, user_payload=user_prompt)
    log_step("parse_invoice", "Invoice payload parsed")
    return {**state, "parsed_invoice": parsed.model_dump()}


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
    result = qb.create_bill(payload)
    log_step("create_bill", f"Bill created successfully. Response keys={list(result.keys())}")
    return {**state, "result": {"action": "bill_created", "response": result}}


def create_invoice_node(state: GraphState) -> GraphState:
    log_step("create_invoice", "Creating Invoice in QuickBooks")
    qb = get_qb_client()
    payload = state["parsed_invoice"]["invoice"]
    result = qb.create_invoice(payload)
    log_step("create_invoice", f"Invoice created successfully. Response keys={list(result.keys())}")
    return {**state, "result": {"action": "invoice_created", "response": result}}


def no_action_node(state: GraphState) -> GraphState:
    parsed = state.get("parsed_bill") or state.get("parsed_invoice") or {}
    reason = "duplicate" if state.get("duplicate_found") else state.get("rationale", "no_action")
    log_step("no_action", f"No action taken. Reason={reason}")
    return {**state, "result": {"action": "no_action", "reason": reason, "parsed": parsed}}

