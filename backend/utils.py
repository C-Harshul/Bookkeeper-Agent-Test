import os
import re
from typing import Any, Dict, List, Optional, Set, Tuple


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


def get_email_body_text(email: Dict[str, Any]) -> str:
    text = str(email.get("text", "") or "").strip()
    if text:
        return text
    html = str(email.get("html", "") or "")
    if not html:
        return ""
    without_tags = re.sub(r"<[^>]+>", " ", html)
    return re.sub(r"\s+", " ", without_tags).strip()


def _body_suggests_vendor_payable(body: str) -> bool:
    t = (body or "").lower()
    markers = (
        "bill from",
        "utility bill",
        "vendor bill",
        "accounts payable",
        "pay this bill",
        "supplier invoice",
        "vendor invoice",
        "payment to vendor",
        "payable to",
        "amount you owe",
        "payable to vendor",
        "wire to vendor",
    )
    return any(m in t for m in markers)


def quick_classify_email_body(body: str) -> Optional[Tuple[str, str]]:
    raw = (body or "").strip()
    if not raw:
        return None
    body = re.sub(r"[\u200b\u200c\ufeff]", "", raw)
    if _body_suggests_vendor_payable(body):
        return (
            "bill",
            "Body contains vendor/bill-style language (payments owed to a supplier); classified as bill.",
        )
    invoice_patterns = [
        r"\binvoice\b",
        r"\binvoice\s*#",
        r"\binvoice\s+number",
        r"\binvoice\s+date",
        r"\bsales\s+invoice\b",
        r"\bcustomer\s+invoice\b",
        r"\btax\s+invoice\b",
        r"\bproforma\s+invoice\b",
        r"\bcredit\s+invoice\b",
        r"\bcommercial\s+invoice\b",
    ]
    for pat in invoice_patterns:
        if re.search(pat, body, re.I):
            return (
                "invoice",
                "The body contains invoice-style wording (e.g. 'Invoice' or invoice number/date); "
                "classified as customer/sales invoice (accounts receivable).",
            )
    return None


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
        account_id = str(account_ref.get("value") or "").strip()
        if not account_id.isdigit() and default_expense_account_id:
            detail["AccountRef"] = {"value": default_expense_account_id}
            line["AccountBasedExpenseLineDetail"] = detail
    return payload


def _to_amount(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value or "").strip()
    if not text:
        return None
    cleaned = re.sub(r"[,$]", "", text)
    m = re.search(r"-?\d+(?:\.\d+)?", cleaned)
    if not m:
        return None
    try:
        return float(m.group(0))
    except ValueError:
        return None


def _extract_amount_by_label(text: str, labels: List[str]) -> float | None:
    for label in labels:
        m = re.search(rf"(?im)^\s*{label}\s*[:\-]\s*\$?\s*([0-9][0-9,]*(?:\.\d+)?)\s*$", text)
        if m:
            return _to_amount(m.group(1))
    return None


def _extract_item_lines(text: str) -> List[Dict[str, Any]]:
    return _extract_line_items_from_body(text)


def _is_summary_or_header_line(stripped: str) -> bool:
    if not stripped:
        return True
    lower = stripped.lower()
    if re.match(
        r"^(subtotal|tax|vat|gst|sales\s+tax|total|amount\s+due|balance\s+due|grand\s+total|invoice\s*#|date|due\s+date)\b",
        lower,
    ):
        return True
    if re.match(r"^(description|item|qty|quantity|rate|amount|price)\s*($|:|\|)", lower):
        return True
    if re.match(r"^(description|item|qty|quantity|rate|amount|price)\s*$", lower):
        return True
    if len(stripped) < 3:
        return True
    return False


def _extract_line_items_from_body(text: str) -> List[Dict[str, Any]]:
    lines: List[Dict[str, Any]] = []
    seen: Set[Tuple[str, float]] = set()

    def push(description: str, amount: float | None) -> None:
        desc = re.sub(r"\s+", " ", (description or "").strip())
        desc = re.sub(r"\s*[-–—]\s*$", "", desc).strip()
        if not desc or len(desc) > 800:
            return
        if _is_summary_or_header_line(desc):
            return
        if amount is None or amount <= 0 or amount > 1e12:
            return
        key = (desc.lower()[:200], round(amount, 2))
        if key in seen:
            return
        seen.add(key)
        lines.append({"description": desc, "amount": amount})

    for raw in text.splitlines():
        s = raw.strip()
        if not s:
            continue
        m = re.search(
            r"^\s*[-*•]\s*(.+?)\s*[-–—]\s*\$?\s*([0-9][0-9,]*(?:\.\d+)?)\s*$",
            s,
        )
        if m:
            push(m.group(1), _to_amount(m.group(2)))
            continue
        m = re.search(
            r"^\s*\d+[\.)]\s*(.+?)\s+\$?\s*([0-9][0-9,]*(?:\.\d+)?)\s*$",
            s,
        )
        if not m:
            m = re.search(
                r"^\s*\d+[\.)]\s*(.+?)\s+[-–—]\s+\$?\s*([0-9][0-9,]*(?:\.\d+)?)\s*$",
                s,
            )
        if m:
            push(m.group(1), _to_amount(m.group(2)))
            continue
        m = re.search(r"^(.+?)\s{2,}\$?\s*([0-9][0-9,]*(?:\.\d+)?)\s*$", s)
        if m and len(m.group(1).strip()) >= 3:
            push(m.group(1), _to_amount(m.group(2)))
            continue
        if "\t" in s:
            parts = [p.strip() for p in s.split("\t") if p.strip()]
            if len(parts) >= 2:
                amt = _to_amount(parts[-1])
                if amt is not None and amt > 0:
                    push(" ".join(parts[:-1]), amt)
                    continue
        if "|" in s and s.count("|") >= 1:
            parts = [p.strip() for p in s.split("|")]
            if len(parts) >= 2:
                amt = _to_amount(parts[-1])
                if amt is not None and amt > 0:
                    desc = " | ".join(parts[:-1]).strip()
                    if desc:
                        push(desc, amt)
                        continue
        m = re.search(
            r"^(.{4,}?)\s+(?:USD|CAD|EUR|GBP|\$|£|€)?\s*([0-9][0-9,]*(?:\.\d{1,2})?)\s*$",
            s,
        )
        if m:
            left, right = m.group(1).strip(), m.group(2)
            if re.search(r"(?i)20\d{2}\s*[-–]\s*\d{1,2}\s*[-–]\s*\d{1,2}", left):
                continue
            if re.match(r"^[\d\s\-/]+$", left):
                continue
            push(left, _to_amount(right))

    return lines


def _item_display_name(it: Dict[str, Any]) -> str:
    return str(it.get("Name") or it.get("FullyQualifiedName") or "").strip()


def _qb_item_allowed_on_transaction_lines(it: Dict[str, Any]) -> bool:
    t = str(it.get("Type", "") or "").strip().lower()
    if t == "category":
        return False
    return True


def _resolve_item_ref(items: List[Dict[str, Any]], description: str, existing: Any) -> Dict[str, str] | None:
    if isinstance(existing, dict):
        iid = str(existing.get("value") or "").strip()
        if iid.isdigit():
            for it in items:
                if str(it.get("Id", "") or "").strip() != iid:
                    continue
                if _qb_item_allowed_on_transaction_lines(it):
                    out: Dict[str, str] = {"value": iid}
                    if existing.get("name"):
                        out["name"] = str(existing["name"])
                    return out
                break

    desc = (description or "").lower().strip()
    if not desc:
        return None
    best: Dict[str, str] | None = None
    best_score = 0
    for it in items:
        if not _qb_item_allowed_on_transaction_lines(it):
            continue
        iid = str(it.get("Id", "") or "").strip()
        if not iid.isdigit():
            continue
        name = _item_display_name(it)
        if not name:
            continue
        nl = name.lower()
        if nl in desc or desc in nl:
            score = len(name)
            if score > best_score:
                best_score = score
                best = {"value": iid, "name": name}
    return best


def _ensure_sellable_item_ref(
    items: List[Dict[str, Any]],
    description: str,
    item_ref: Any,
    default_item: Dict[str, str],
) -> Dict[str, str]:
    resolved = _resolve_item_ref(items, description, item_ref)
    if resolved and not _ref_id_missing(resolved):
        return resolved
    if not _ref_id_missing(default_item):
        return dict(default_item)
    return {}


def _default_account_ref(accounts: List[Dict[str, Any]]) -> Dict[str, str]:
    configured = os.getenv("QB_DEFAULT_EXPENSE_ACCOUNT_ID", "").strip()
    if configured:
        return {"value": configured}
    for account in accounts:
        account_type = str(account.get("AccountType", "")).lower()
        classification = str(account.get("Classification", "")).lower()
        if "expense" in account_type or "expense" in classification:
            account_id = str(account.get("Id", "")).strip()
            if account_id:
                return {"value": account_id}
    return {"value": ""}


def _default_item_ref(items: List[Dict[str, Any]]) -> Dict[str, str]:
    configured = os.getenv("QB_DEFAULT_INVOICE_ITEM_ID", "").strip()
    if configured.isdigit():
        for it in items:
            if str(it.get("Id", "") or "").strip() != configured:
                continue
            if _qb_item_allowed_on_transaction_lines(it):
                name = _item_display_name(it)
                return {"value": configured, **({"name": name} if name else {})}
            break
    for it in items:
        if not _qb_item_allowed_on_transaction_lines(it):
            continue
        iid = str(it.get("Id", "") or "").strip()
        if iid.isdigit():
            name = _item_display_name(it)
            return {"value": iid, **({"name": name} if name else {})}
    return {"value": ""}


def _lines_from_body_with_items(
    body_rows: List[Dict[str, Any]],
    account_ref: Dict[str, str],
    items: List[Dict[str, Any]],
    default_item: Dict[str, str],
) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for row in body_rows:
        desc = str(row.get("description") or "")
        amount = float(row["amount"])
        ir = _ensure_sellable_item_ref(items, desc, {}, default_item)
        if not _ref_id_missing(ir):
            out.append(
                {
                    "Description": desc,
                    "Amount": amount,
                    "DetailType": "ItemBasedExpenseLineDetail",
                    "ItemBasedExpenseLineDetail": {
                        "ItemRef": ir,
                        "UnitPrice": amount,
                        "Qty": 1.0,
                        "BillableStatus": "NotBillable",
                        "TaxCodeRef": {"value": "NON"},
                    },
                }
            )
        else:
            out.append(_as_account_line(desc, amount, account_ref))
    return out


def _as_sales_item_line(description: str, amount: float, item_ref: Dict[str, str]) -> Dict[str, Any]:
    return {
        "Description": description,
        "Amount": amount,
        "DetailType": "SalesItemLineDetail",
        "SalesItemLineDetail": {
            "ItemRef": item_ref,
            "Qty": 1.0,
            "UnitPrice": amount,
        },
    }


def _lines_from_body_for_invoice(
    body_rows: List[Dict[str, Any]],
    items: List[Dict[str, Any]],
    default_item: Dict[str, str],
) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for row in body_rows:
        desc = str(row.get("description") or "")
        amount = float(row["amount"])
        ir = _ensure_sellable_item_ref(items, desc, {}, default_item)
        if _ref_id_missing(ir):
            continue
        out.append(_as_sales_item_line(desc, amount, ir))
    return out


def _as_account_line(description: str, amount: float, account_ref: Dict[str, str]) -> Dict[str, Any]:
    return {
        "Description": description,
        "Amount": amount,
        "DetailType": "AccountBasedExpenseLineDetail",
        "AccountBasedExpenseLineDetail": {
            "BillableStatus": "NotBillable",
            "AccountRef": account_ref,
            "TaxCodeRef": {"value": "NON"},
        },
    }


def _drop_none_values(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: _drop_none_values(v) for k, v in obj.items() if v is not None}
    if isinstance(obj, list):
        return [_drop_none_values(x) for x in obj]
    return obj


def _ref_id_missing(ref: Any) -> bool:
    if not isinstance(ref, dict):
        return True
    v = ref.get("value")
    if v is None:
        return True
    s = str(v).strip()
    return not s


def _vendor_display_name(v: Dict[str, Any]) -> str:
    return str(v.get("DisplayName") or v.get("CompanyName") or v.get("FullyQualifiedName") or "").strip()


def _resolve_vendor_ref(
    vendors: List[Dict[str, Any]],
    email_text: str,
    existing: Any,
) -> Dict[str, str] | None:
    if isinstance(existing, dict):
        vid = str(existing.get("value") or "").strip()
        if vid.isdigit():
            out: Dict[str, str] = {"value": vid}
            if existing.get("name"):
                out["name"] = str(existing["name"])
            return out

    blob = email_text.lower()
    vendor_label = re.search(r"(?im)^\s*Vendor\s*[:\-]\s*(.+?)\s*$", email_text)
    hints: List[str] = []
    if vendor_label:
        hints.append(vendor_label.group(1).strip())

    best: Dict[str, str] | None = None
    best_score = 0
    for v in vendors:
        vid = str(v.get("Id", "") or "").strip()
        if not vid.isdigit():
            continue
        name = _vendor_display_name(v)
        if not name:
            continue
        nl = name.lower()
        for hint in hints:
            if not hint:
                continue
            hl = hint.lower().strip()
            if hl and (hl in nl or nl in hl or hl in blob):
                score = min(len(nl), len(hl)) if hl in nl or nl in hl else len(nl)
                if score > best_score:
                    best_score = score
                    best = {"value": vid, "name": name}
        if nl in blob:
            score = len(name)
            if score > best_score:
                best_score = score
                best = {"value": vid, "name": name}
    return best


def _customer_display_name(c: Dict[str, Any]) -> str:
    return str(c.get("DisplayName") or c.get("CompanyName") or c.get("FullyQualifiedName") or "").strip()


def _resolve_customer_ref(
    customers: List[Dict[str, Any]],
    email_text: str,
    existing: Any,
) -> Dict[str, str] | None:
    if isinstance(existing, dict):
        cid = str(existing.get("value") or "").strip()
        if cid.isdigit():
            out: Dict[str, str] = {"value": cid}
            if existing.get("name"):
                out["name"] = str(existing["name"])
            return out

    blob = email_text.lower()
    hints: List[str] = []
    for pattern in (
        r"(?im)^\s*Bill\s+To\s*[:\-]\s*(.+?)\s*$",
        r"(?im)^\s*Customer\s*[:\-]\s*(.+?)\s*$",
        r"(?im)^\s*Invoice\s+To\s*[:\-]\s*(.+?)\s*$",
        r"(?im)^\s*Sold\s+To\s*[:\-]\s*(.+?)\s*$",
    ):
        m = re.search(pattern, email_text)
        if m:
            hints.append(m.group(1).strip())

    best: Dict[str, str] | None = None
    best_score = 0
    for c in customers:
        cid = str(c.get("Id", "") or "").strip()
        if not cid.isdigit():
            continue
        name = _customer_display_name(c)
        if not name:
            continue
        nl = name.lower()
        for hint in hints:
            if not hint:
                continue
            hl = hint.lower().strip()
            if hl and (hl in nl or nl in hl or hl in blob):
                score = min(len(nl), len(hl)) if hl in nl or nl in hl else len(nl)
                if score > best_score:
                    best_score = score
                    best = {"value": cid, "name": name}
        if nl in blob:
            score = len(name)
            if score > best_score:
                best_score = score
                best = {"value": cid, "name": name}
    return best


def _extract_invoice_txn_date(email_text: str) -> str | None:
    m = re.search(r"(?im)^\s*(?:Invoice\s+)?Date\s*[:\-]\s*(.+?)\s*$", email_text)
    if m:
        return m.group(1).strip()
    m2 = re.search(r"(?im)^\s*Date\s+Of\s+Invoice\s*[:\-]\s*(.+?)\s*$", email_text)
    if m2:
        return m2.group(1).strip()
    return None


def _extract_invoice_doc_number(email_text: str) -> str | None:
    m = re.search(r"(?im)^\s*Invoice\s+Number\s*[:\-]\s*([A-Za-z0-9\-]+)\s*$", email_text)
    if m:
        return m.group(1).strip()
    m2 = re.search(r"(?im)^\s*Invoice\s*#\s*[:\-]?\s*([A-Za-z0-9\-]+)\s*$", email_text)
    if m2:
        return m2.group(1).strip()
    m3 = re.search(r"(?im)^\s*Invoice\s+No\.?\s*[:\-]?\s*([A-Za-z0-9\-]+)\s*$", email_text)
    if m3:
        return m3.group(1).strip()
    return None


def _extract_bill_dates(email_text: str) -> tuple[str | None, str | None]:
    txn: str | None = None
    due: str | None = None
    m = re.search(r"(?im)^\s*Bill\s+Date\s*[:\-]\s*(.+?)\s*$", email_text)
    if m:
        txn = m.group(1).strip()
    m2 = re.search(r"(?im)^\s*Due\s+Date\s*[:\-]\s*(.+?)\s*$", email_text)
    if m2:
        due = m2.group(1).strip()
    return txn, due


def _extract_amount_hints_from_body(email_text: str) -> Dict[str, Any]:
    hints: Dict[str, Any] = {}
    subtotal = _extract_amount_by_label(
        email_text,
        ["Subtotal", "Sub-total", "Sub total", "Sub-Total"],
    )
    if subtotal is not None:
        hints["subtotal"] = subtotal
    tax = _extract_amount_by_label(
        email_text,
        ["Tax", "VAT", "GST", "Sales tax", "Sales Tax"],
    )
    if tax is not None:
        hints["tax"] = tax
    total = _extract_amount_by_label(
        email_text,
        ["Total", "Amount Due", "Balance Due", "Grand Total"],
    )
    if total is not None:
        hints["total"] = total
    return hints


def build_bill_payload_from_email(
    email: Dict[str, Any],
    parsed: Dict[str, Any],
    *,
    items: List[Dict[str, Any]],
    vendors: List[Dict[str, Any]],
    accounts: List[Dict[str, Any]],
) -> Dict[str, Any]:
    body = get_email_body_text(email)
    bill: Dict[str, Any] = dict(parsed.get("bill") or {})
    duplicate_check: Dict[str, Any] = dict(parsed.get("duplicate_check") or {})
    rationale = str(parsed.get("rationale") or "").strip()

    for key, val in _extract_amount_hints_from_body(body).items():
        if val is not None and duplicate_check.get(key) is None:
            duplicate_check[key] = val

    vr = _resolve_vendor_ref(vendors, body, bill.get("VendorRef"))
    if vr:
        bill["VendorRef"] = vr

    txn, due = _extract_bill_dates(body)
    if txn and not bill.get("TxnDate"):
        bill["TxnDate"] = txn
    if due and not bill.get("DueDate"):
        bill["DueDate"] = due

    if not bill.get("CurrencyRef"):
        bill["CurrencyRef"] = {"value": "USD"}

    account_ref = _default_account_ref(accounts)
    default_item = _default_item_ref(items)
    body_rows = _extract_line_items_from_body(body)
    llm_lines = bill.get("Line") or []

    if body_rows and not llm_lines:
        bill["Line"] = _lines_from_body_with_items(body_rows, account_ref, items, default_item)
        if not rationale:
            rationale = "Line items built from email body (LLM returned no lines)."

    bill = _drop_none_values(bill)
    return {"bill": bill, "duplicate_check": duplicate_check, "rationale": rationale}


def build_invoice_payload_from_email(
    email: Dict[str, Any],
    parsed: Dict[str, Any],
    *,
    items: List[Dict[str, Any]],
    customers: List[Dict[str, Any]],
) -> Dict[str, Any]:
    body = get_email_body_text(email)
    inv: Dict[str, Any] = dict(parsed.get("invoice") or {})
    duplicate_check: Dict[str, Any] = dict(parsed.get("duplicate_check") or {})
    rationale = str(parsed.get("rationale") or "").strip()

    for key, val in _extract_amount_hints_from_body(body).items():
        if val is not None and duplicate_check.get(key) is None:
            duplicate_check[key] = val

    cr = _resolve_customer_ref(customers, body, inv.get("CustomerRef"))
    if cr:
        inv["CustomerRef"] = cr

    if not inv.get("DocNumber"):
        dn = _extract_invoice_doc_number(body)
        if dn:
            inv["DocNumber"] = dn

    if not inv.get("TxnDate"):
        td = _extract_invoice_txn_date(body)
        if td:
            inv["TxnDate"] = td

    default_item = _default_item_ref(items)
    body_rows = _extract_line_items_from_body(body)
    llm_lines = inv.get("Line") or []

    if body_rows and not llm_lines:
        inv["Line"] = _lines_from_body_for_invoice(body_rows, items, default_item)
        if not rationale:
            rationale = "Line items built from email body (LLM returned no lines)."

    inv = _drop_none_values(inv)
    return {"invoice": inv, "duplicate_check": duplicate_check, "rationale": rationale}

