import os
from typing import Any, Dict, List


def log_step(step: str, message: str) -> None:
    print(f"[STEP:{step}] {message}")


def contains_address(haystack: str, needle: str) -> bool:
    return needle.lower() in (haystack or "").lower()


def should_track_email(email: Dict[str, Any], target_email: str) -> bool:
    if not target_email:
        return True
    fields = [
        email.get("from", ""),
        email.get("to", ""),
        email.get("cc", ""),
        email.get("bcc", ""),
        email.get("subject", ""),
        email.get("text", ""),
        email.get("html", ""),
    ]
    return any(contains_address(str(value), target_email) for value in fields)


def build_email_blob(email: Dict[str, Any]) -> Dict[str, str]:
    return {
        "subject": str(email.get("subject", "")),
        "from": str(email.get("from", "")),
        "date": str(email.get("date", "")),
        "html": str(email.get("html", "")),
        "text": str(email.get("text", "")),
    }


def summarize_counts(prefix: str, data: Dict[str, List[Dict[str, Any]]]) -> None:
    formatted = ", ".join(f"{key}={len(value)}" for key, value in data.items())
    log_step(prefix, f"Loaded {prefix.replace('_', ' ')}: {formatted}")


def detect_bill_duplicate(bill: Dict[str, Any], existing_bills: List[Dict[str, Any]]) -> bool:
    vendor = (bill.get("VendorRef") or {}).get("value")
    txn_date = bill.get("TxnDate")
    amount = sum((line or {}).get("Amount", 0) for line in bill.get("Line", []))
    for existing in existing_bills:
        same_vendor = ((existing.get("VendorRef") or {}).get("value") == vendor)
        same_date = existing.get("TxnDate") == txn_date
        existing_total = existing.get("TotalAmt")
        same_amount = existing_total is not None and abs(float(existing_total) - float(amount)) < 0.01
        if same_vendor and same_date and same_amount:
            return True
    return False


def detect_invoice_duplicate(invoice: Dict[str, Any], existing_invoices: List[Dict[str, Any]]) -> bool:
    doc = invoice.get("DocNumber")
    customer = (invoice.get("CustomerRef") or {}).get("value")
    amount = sum((line or {}).get("Amount", 0) for line in invoice.get("Line", []))
    for existing in existing_invoices:
        same_doc = existing.get("DocNumber") == doc if doc else False
        same_customer = ((existing.get("CustomerRef") or {}).get("value") == customer)
        existing_total = existing.get("TotalAmt")
        same_amount = existing_total is not None and abs(float(existing_total) - float(amount)) < 0.01
        if same_doc or (same_customer and same_amount):
            return True
    return False


def sanitize_bill_payload(payload: Dict[str, Any], accounts: List[Dict[str, Any]]) -> Dict[str, Any]:
    default_expense_account_id = os.getenv("QB_DEFAULT_EXPENSE_ACCOUNT_ID", "").strip()
    if not default_expense_account_id:
        for account in accounts:
            account_type = str(account.get("AccountType", "")).lower()
            classification = str(account.get("Classification", "")).lower()
            if "expense" in account_type or "expense" in classification:
                default_expense_account_id = str(account.get("Id", "")).strip()
                break

    for line in payload.get("Line", []):
        if line.get("DetailType") != "AccountBasedExpenseLineDetail":
            continue
        detail = line.get("AccountBasedExpenseLineDetail") or {}
        account_ref = detail.get("AccountRef") or {}
        account_id = str(account_ref.get("value", "")).strip()
        if not account_id.isdigit() and default_expense_account_id:
            detail["AccountRef"] = {"value": default_expense_account_id}
            line["AccountBasedExpenseLineDetail"] = detail
    return payload

