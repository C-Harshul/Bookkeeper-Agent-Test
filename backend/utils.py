import json
import os
import re
from datetime import datetime
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


def _pick_ref(ref: Any) -> Dict[str, Any] | None:
    if isinstance(ref, dict) and (ref.get("value") is not None or ref.get("name") is not None):
        out: Dict[str, Any] = {}
        if ref.get("value") is not None:
            out["value"] = ref["value"]
        if ref.get("name") is not None:
            out["name"] = ref["name"]
        return out
    return None


def compact_qb_item_for_llm(row: Dict[str, Any]) -> Dict[str, Any]:
    """Strip noisy fields but keep every item so the LLM can choose ItemRef and expense behavior."""
    out: Dict[str, Any] = {}
    for key in (
        "Id",
        "Name",
        "FullyQualifiedName",
        "Type",
        "Active",
        "UnitPrice",
        "PurchaseCost",
        "Taxable",
        "TrackQtyOnHand",
        "QtyOnHand",
    ):
        if key in row and row[key] is not None:
            out[key] = row[key]
    for ref_key in ("IncomeAccountRef", "ExpenseAccountRef", "AssetAccountRef"):
        pr = _pick_ref(row.get(ref_key))
        if pr:
            out[ref_key] = pr
    return out


def compact_qb_vendor_for_llm(row: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for key in ("Id", "DisplayName", "CompanyName", "FullyQualifiedName", "Active"):
        if key in row and row[key] is not None:
            out[key] = row[key]
    pe = row.get("PrimaryEmailAddr")
    if isinstance(pe, dict) and pe.get("Address"):
        out["PrimaryEmailAddr"] = {"Address": pe.get("Address")}
    return out


def compact_qb_account_for_llm(row: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for key in (
        "Id",
        "Name",
        "FullyQualifiedName",
        "AccountType",
        "AccountSubType",
        "Classification",
        "Active",
        "CurrentBalance",
    ):
        if key in row and row[key] is not None:
            out[key] = row[key]
    return out


def compact_qb_account_for_bill_llm(row: Dict[str, Any]) -> Dict[str, Any]:
    """Like compact_qb_account_for_llm but never send balances — they invite spurious 'analysis' from the LLM."""
    out: Dict[str, Any] = {}
    for key in (
        "Id",
        "Name",
        "FullyQualifiedName",
        "AccountType",
        "AccountSubType",
        "Classification",
        "Active",
    ):
        if key in row and row[key] is not None:
            out[key] = row[key]
    return out


def _account_eligible_for_bill_expense_reference(row: Dict[str, Any]) -> bool:
    """Only accounts useful for bill / tax lines; drop Income, Bank, A/R, etc."""
    cls = str(row.get("Classification") or "").strip().lower()
    at = str(row.get("AccountType") or "").strip().lower()
    st = str(row.get("AccountSubType") or "").strip().lower()
    if cls == "revenue" or at == "income":
        return False
    if cls == "equity":
        return False
    if at in ("bank", "accounts receivable", "credit card", "fixed asset", "accounts payable"):
        return False
    if "fixed asset" in at:
        return False
    if cls == "asset":
        return False
    if cls == "expense" or "expense" in at or "cost of goods sold" in at:
        return True
    if at == "other expense":
        return True
    if at == "other current liability" and ("tax" in st.lower() or "globaltax" in st.lower()):
        return True
    return False


def compact_qb_customer_for_llm(row: Dict[str, Any]) -> Dict[str, Any]:
    """Identity fields only — no Balance / OpenBalance (avoids conflating AR history with this invoice)."""
    out: Dict[str, Any] = {}
    for key in ("Id", "DisplayName", "CompanyName", "FullyQualifiedName", "Active"):
        if key in row and row[key] is not None:
            out[key] = row[key]
    return out


def build_bill_llm_reference(
    items: List[Dict[str, Any]],
    vendors: List[Dict[str, Any]],
    accounts: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """All items and vendors; accounts limited to expense/tax-style rows (no balances)."""
    acct_rows = [
        compact_qb_account_for_bill_llm(a)
        for a in accounts
        if isinstance(a, dict) and _account_eligible_for_bill_expense_reference(a)
    ]
    return {
        "items": [compact_qb_item_for_llm(i) for i in items if isinstance(i, dict)],
        "vendors": [compact_qb_vendor_for_llm(v) for v in vendors if isinstance(v, dict)],
        "accounts": acct_rows,
        "accounts_note": (
            "accounts lists only expense, COGS, other expense, and tax-payable-style liabilities "
            "suitable for bill lines — not the full chart of accounts."
        ),
    }


def build_invoice_llm_reference(
    items: List[Dict[str, Any]],
    customers: List[Dict[str, Any]],
) -> Dict[str, Any]:
    return {
        "items": [compact_qb_item_for_llm(i) for i in items if isinstance(i, dict)],
        "customers": [compact_qb_customer_for_llm(c) for c in customers if isinstance(c, dict)],
        "customers_note": "Customer rows omit open balances and balances — match CustomerRef by identity only.",
    }


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
    """
    Match a specific invoice identity only — not "same customer + same total", which would treat
    unrelated invoices as duplicates (aggregating distinct bills into one duplicate hit).
    """
    doc = str(invoice.get("DocNumber") or "").strip()
    customer = (invoice.get("CustomerRef") or {}).get("value")
    txn_date = invoice.get("TxnDate")
    try:
        amount = sum(float((line or {}).get("Amount", 0) or 0) for line in invoice.get("Line", []))
    except (TypeError, ValueError):
        amount = 0.0

    for existing in existing_invoices:
        ex_doc = str(existing.get("DocNumber") or "").strip()
        ex_cust = (existing.get("CustomerRef") or {}).get("value")

        if doc and ex_doc == doc:
            if customer is None or ex_cust is None or customer == ex_cust:
                return True
            continue

        if not doc:
            existing_total = existing.get("TotalAmt")
            try:
                same_amount = (
                    existing_total is not None and abs(float(existing_total) - float(amount)) < 0.01
                )
            except (TypeError, ValueError):
                same_amount = False
            same_customer = customer is not None and ex_cust == customer
            same_date = bool(txn_date) and existing.get("TxnDate") == txn_date
            if same_customer and same_amount and same_date:
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


def _strip_markdown_wrapping(s: str) -> str:
    t = (s or "").strip()
    t = re.sub(r"^\*+\s*", "", t)
    t = re.sub(r"\s*\*+$", "", t)
    t = re.sub(r"^_+\s*", "", t)
    t = re.sub(r"\s*_+$", "", t)
    return t.strip()


def _is_summary_or_header_line(stripped: str) -> bool:
    if not stripped:
        return True
    candidates = [stripped.lower(), _strip_markdown_wrapping(stripped).lower()]
    for lower in candidates:
        if re.match(
            r"^(subtotal|tax\b|vat\b|gst\b|sales\s+tax|total\b|amount\s+due|balance\s+due|grand\s+total|"
            r"open\s+balance|previous\s+balance|prior\s+balance|balance\s+forward|"
            r"invoice\s*#|date\b|due\s+date|bill\s+date|invoice\s+date|payment\s+due)\b",
            lower,
        ):
            return True
        if re.match(r"^tax\s*\(", lower):
            return True
    lower = stripped.lower()
    if re.match(r"^(description|item|qty|quantity|rate|amount|price)\s*($|:|\|)", lower):
        return True
    if re.match(r"^(description|item|qty|quantity|rate|amount|price)\s*$", lower):
        return True
    if len(stripped) < 3:
        return True
    return False


def _description_looks_like_open_balance_row(desc: str) -> bool:
    """AR statement rows — not part of the current invoice charges."""
    inner = _strip_markdown_wrapping(desc).lower()
    if re.search(r"\bopen\s+balance\b", inner):
        return True
    if re.search(r"\b(previous|prior)\s+balance\b", inner):
        return True
    if re.match(r"^balance\s+forward\b", inner):
        return True
    if re.search(r"\baging\s+balance\b", inner):
        return True
    if re.search(r"\bstatement\s+balance\b", inner) and "due" not in inner:
        return True
    return False


def _invoice_line_description(line: Dict[str, Any]) -> str:
    if not isinstance(line, dict):
        return ""
    d = line.get("Description")
    if d is not None and str(d).strip():
        return str(d).strip()
    det = line.get("SalesItemLineDetail") or {}
    if isinstance(det, dict):
        d2 = det.get("Description")
        if d2 is not None and str(d2).strip():
            return str(d2).strip()
    return ""


def _strip_open_balance_invoice_lines(inv: Dict[str, Any]) -> bool:
    lines_in = inv.get("Line")
    if not isinstance(lines_in, list):
        return False
    kept: List[Dict[str, Any]] = []
    removed = False
    for line in lines_in:
        if not isinstance(line, dict):
            continue
        desc = _invoice_line_description(line)
        if _description_looks_like_open_balance_row(desc):
            removed = True
            continue
        kept.append(line)
    if removed:
        inv["Line"] = kept
    return removed


def _description_looks_like_bill_metadata_row(desc: str) -> bool:
    """True for date/summary rows that should never become expense lines."""
    inner = _strip_markdown_wrapping(desc).lower()
    if re.search(r"\b(bill date|due date|invoice date|payment due)\b", inner):
        return True
    if re.search(
        r"\b(january|february|march|april|may|june|july|august|september|october|november|december)\b",
        inner,
    ) and re.search(r"\b20\d{2}\b", inner):
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
        if _description_looks_like_bill_metadata_row(desc):
            return
        if _description_looks_like_open_balance_row(desc):
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
            amt_guess = _to_amount(right)
            if amt_guess is not None and 1990 <= amt_guess <= 2035 and float(int(amt_guess)) == amt_guess:
                left_l = left.lower()
                if re.search(
                    r"(january|february|march|april|may|june|july|august|september|october|november|december|"
                    r"bill\s+date|due\s+date|invoice\s+date|payment\s+due)",
                    left_l,
                ):
                    continue
            push(left, amt_guess)

    return lines


def _item_display_name(it: Dict[str, Any]) -> str:
    return str(it.get("Name") or it.get("FullyQualifiedName") or "").strip()


def _qb_item_allowed_on_transaction_lines(it: Dict[str, Any]) -> bool:
    t = str(it.get("Type", "") or "").strip().lower()
    if t == "category":
        return False
    return True


def _qb_item_has_purchase_expense_account(it: Dict[str, Any]) -> bool:
    """QBO rejects ItemBasedExpenseLineDetail if the item has no purchase/expense account."""
    er = it.get("ExpenseAccountRef")
    return isinstance(er, dict) and str(er.get("value", "")).strip().isdigit()


def _resolve_item_ref(
    items: List[Dict[str, Any]],
    description: str,
    existing: Any,
    *,
    require_purchase_expense_account: bool = False,
) -> Dict[str, str] | None:
    if isinstance(existing, dict):
        iid = str(existing.get("value") or "").strip()
        if iid.isdigit():
            for it in items:
                if str(it.get("Id", "") or "").strip() != iid:
                    continue
                if not _qb_item_allowed_on_transaction_lines(it):
                    break
                if require_purchase_expense_account and not _qb_item_has_purchase_expense_account(it):
                    break
                out: Dict[str, str] = {"value": iid}
                if existing.get("name"):
                    out["name"] = str(existing["name"])
                return out

    desc = (description or "").lower().strip()
    if not desc:
        return None
    best: Dict[str, str] | None = None
    best_score = 0
    for it in items:
        if not _qb_item_allowed_on_transaction_lines(it):
            continue
        if require_purchase_expense_account and not _qb_item_has_purchase_expense_account(it):
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


def _default_bill_item_ref(items: List[Dict[str, Any]]) -> Dict[str, str]:
    configured = os.getenv("QB_DEFAULT_BILL_ITEM_ID", "").strip()
    if not configured:
        configured = os.getenv("QB_DEFAULT_INVOICE_ITEM_ID", "").strip()
    if configured.isdigit():
        for it in items:
            if str(it.get("Id", "") or "").strip() != configured:
                continue
            if _qb_item_allowed_on_transaction_lines(it) and _qb_item_has_purchase_expense_account(it):
                name = _item_display_name(it)
                return {"value": configured, **({"name": name} if name else {})}
            break
    for it in items:
        if not _qb_item_allowed_on_transaction_lines(it):
            continue
        if not _qb_item_has_purchase_expense_account(it):
            continue
        iid = str(it.get("Id", "") or "").strip()
        if iid.isdigit():
            name = _item_display_name(it)
            return {"value": iid, **({"name": name} if name else {})}
    return {"value": ""}


def _ensure_bill_purchase_item_ref(
    items: List[Dict[str, Any]],
    description: str,
    item_ref: Any,
    default_item: Dict[str, str],
) -> Dict[str, str]:
    resolved = _resolve_item_ref(
        items,
        description,
        item_ref,
        require_purchase_expense_account=True,
    )
    if resolved and not _ref_id_missing(resolved):
        return resolved
    if not _ref_id_missing(default_item):
        did = str(default_item.get("value", "")).strip()
        for it in items:
            if str(it.get("Id", "") or "").strip() != did:
                continue
            if _qb_item_has_purchase_expense_account(it):
                return dict(default_item)
            break
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


def clamp_bill_agent_output_to_quickbooks(
    agent_out: Dict[str, Any],
    *,
    items: List[Dict[str, Any]],
    vendors: List[Dict[str, Any]],
    accounts: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """
    Remove or replace any VendorRef / ItemRef / AccountRef not present in QuickBooks API
    responses so we never POST hallucinated Ids.
    """
    data: Dict[str, Any] = json.loads(json.dumps(agent_out, default=str))
    bill = data.get("bill")
    if not isinstance(bill, dict):
        return data

    vendor_ids = {
        str(v.get("Id", "")).strip()
        for v in vendors
        if isinstance(v, dict) and str(v.get("Id", "")).strip().isdigit()
    }
    account_ids = {
        str(a.get("Id", "")).strip()
        for a in accounts
        if isinstance(a, dict) and str(a.get("Id", "")).strip().isdigit()
    }
    sellable_item_ids: Set[str] = set()
    for it in items:
        if not isinstance(it, dict):
            continue
        iid = str(it.get("Id", "")).strip()
        if (
            iid.isdigit()
            and _qb_item_allowed_on_transaction_lines(it)
            and _qb_item_has_purchase_expense_account(it)
        ):
            sellable_item_ids.add(iid)

    default_acct = _default_account_ref(accounts)
    default_id = str(default_acct.get("value") or "").strip()
    touched = False

    vr = bill.get("VendorRef")
    if isinstance(vr, dict):
        vid = str(vr.get("value") or "").strip()
        if not vid.isdigit() or vid not in vendor_ids:
            bill["VendorRef"] = None
            touched = True

    ap_ref = bill.get("APAccountRef")
    if isinstance(ap_ref, dict):
        aid = str(ap_ref.get("value") or "").strip()
        if aid and (not aid.isdigit() or aid not in account_ids):
            bill["APAccountRef"] = None
            touched = True

    lines_in = bill.get("Line")
    lines: List[Any] = lines_in if isinstance(lines_in, list) else []
    new_lines: List[Dict[str, Any]] = []

    for line in lines:
        if not isinstance(line, dict):
            continue
        dt = line.get("DetailType")
        try:
            amt = float(line.get("Amount", 0) or 0)
        except (TypeError, ValueError):
            amt = 0.0
        desc = line.get("Description")

        if dt == "ItemBasedExpenseLineDetail":
            detail = line.get("ItemBasedExpenseLineDetail") or {}
            ir = detail.get("ItemRef") or {}
            iid = str(ir.get("value") or "").strip()
            if iid in sellable_item_ids:
                new_lines.append(line)
            else:
                touched = True
                new_lines.append(
                    {
                        "Description": desc,
                        "Amount": amt,
                        "DetailType": "AccountBasedExpenseLineDetail",
                        "AccountBasedExpenseLineDetail": {
                            "BillableStatus": "NotBillable",
                            "AccountRef": dict(default_acct),
                            "TaxCodeRef": {"value": "NON"},
                        },
                    },
                )
        elif dt == "AccountBasedExpenseLineDetail":
            detail = line.get("AccountBasedExpenseLineDetail") or {}
            ar = detail.get("AccountRef") or {}
            aid = str(ar.get("value") or "").strip()
            if aid.isdigit() and aid in account_ids:
                new_lines.append(line)
            else:
                touched = True
                new_lines.append(
                    {
                        **line,
                        "AccountBasedExpenseLineDetail": {
                            **detail,
                            "AccountRef": dict(default_acct),
                        },
                    }
                )
        else:
            new_lines.append(line)

    bill["Line"] = new_lines
    data["bill"] = bill

    if touched:
        note = (
            "QuickBooks IDs were validated against API data only; "
            "invalid vendor/item/account refs were cleared or mapped to the default expense account."
        )
        prev = (data.get("rationale") or "").strip()
        data["rationale"] = f"{prev} {note}".strip() if prev else note

    return data


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
        ir = _ensure_bill_purchase_item_ref(items, desc, {}, default_item)
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


def normalize_quickbooks_date(value: Any) -> str | None:
    """Normalize common email/LLM date strings to QBO yyyy-mm-dd."""
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    if re.match(r"^\d{4}-\d{2}-\d{2}$", s):
        return s
    for fmt in (
        "%B %d, %Y",
        "%b %d, %Y",
        "%B %d %Y",
        "%b %d %Y",
        "%m/%d/%Y",
        "%m/%d/%y",
        "%d/%m/%Y",
        "%Y-%m-%d",
    ):
        try:
            return datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            continue
    m = re.match(r"^(\d{1,2})[./-](\d{1,2})[./-](\d{4})$", s)
    if m:
        a, b, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        for month, day in (a, b), (b, a):
            if 1 <= month <= 12 and 1 <= day <= 31:
                try:
                    return datetime(y, month, day).date().isoformat()
                except ValueError:
                    continue
    return None


def _extract_bill_dates(email_text: str) -> tuple[str | None, str | None]:
    txn: str | None = None
    due: str | None = None
    for pat in (
        r"(?im)^\s*Bill\s+Date\s*[:\-]\s*(.+?)\s*$",
        r"(?im)^\s*Invoice\s+Date\s*[:\-]\s*(.+?)\s*$",
        r"(?im)^\s*Statement\s+Date\s*[:\-]\s*(.+?)\s*$",
    ):
        m = re.search(pat, email_text)
        if m:
            txn = m.group(1).strip()
            break
    for pat in (
        r"(?im)^\s*Due\s+Date\s*[:\-]\s*(.+?)\s*$",
        r"(?im)^\s*Payment\s+Due\s*(?:Date)?\s*[:\-]\s*(.+?)\s*$",
    ):
        m = re.search(pat, email_text)
        if m:
            due = m.group(1).strip()
            break
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


def _line_sum_amounts(lines: List[Dict[str, Any]]) -> float:
    return sum(float((line or {}).get("Amount", 0) or 0) for line in lines)


def _description_suggests_tax_line(desc: str) -> bool:
    d = desc.lower().strip()
    if not d:
        return False
    return bool(re.match(r"^(tax|vat|gst|sales\s+tax)\b", d) or re.search(r"\b(sales\s+tax|vat|gst)\b", d))


def _prune_spurious_bill_expense_lines(bill: Dict[str, Any]) -> bool:
    """Drop lines that are clearly totals, tax labels, or dates mistaken for line items."""
    lines_in = bill.get("Line")
    if not isinstance(lines_in, list):
        return False
    kept: List[Dict[str, Any]] = []
    removed = False
    for line in lines_in:
        if not isinstance(line, dict):
            continue
        desc = str(line.get("Description") or "")
        if _is_summary_or_header_line(desc) or _description_looks_like_bill_metadata_row(desc):
            removed = True
            continue
        kept.append(line)
    if removed:
        bill["Line"] = kept
    return removed


def _strip_bill_tax_expense_lines(bill: Dict[str, Any]) -> bool:
    """Remove expense lines that represent tax; QBO should compute tax via TaxCodeRef + GlobalTaxCalculation."""
    lines_in = bill.get("Line")
    if not isinstance(lines_in, list):
        return False
    kept: List[Dict[str, Any]] = []
    removed = False
    for line in lines_in:
        if not isinstance(line, dict):
            continue
        desc = str((line or {}).get("Description") or "")
        if _description_suggests_tax_line(desc):
            removed = True
            continue
        kept.append(line)
    if removed:
        bill["Line"] = kept
    return removed


def _maybe_scale_bill_lines_to_subtotal(bill: Dict[str, Any], duplicate_check: Dict[str, Any]) -> bool:
    """
    If lines sum to invoice total but email gives subtotal+tax=total, scale line Amounts to subtotal (pre-tax).
    """
    lines = list(bill.get("Line") or [])
    if not lines:
        return False
    sub_raw = duplicate_check.get("subtotal")
    total_raw = duplicate_check.get("total")
    tax_raw = duplicate_check.get("tax")
    if sub_raw is None or total_raw is None or tax_raw is None:
        return False
    try:
        sub_amt = float(sub_raw)
        total_amt = float(total_raw)
        tax_amt = float(tax_raw)
    except (TypeError, ValueError):
        return False
    if sub_amt <= 0 or total_amt <= 0 or tax_amt <= 0:
        return False
    if abs((sub_amt + tax_amt) - total_amt) > 0.08:
        return False
    line_sum = _line_sum_amounts(lines)
    if line_sum <= 0:
        return False
    if abs(line_sum - sub_amt) <= 0.08:
        return False
    if abs(line_sum - total_amt) > 0.08:
        return False
    factor = sub_amt / line_sum
    touched = False
    for line in lines:
        if not isinstance(line, dict):
            continue
        try:
            amt = float(line.get("Amount", 0) or 0)
        except (TypeError, ValueError):
            continue
        if amt <= 0:
            continue
        line["Amount"] = round(amt * factor, 2)
        touched = True
    if touched:
        bill["Line"] = lines
    return touched


def _tax_code_id_set(tax_codes: List[Dict[str, Any]]) -> Set[str]:
    return {
        str(tc.get("Id", "")).strip()
        for tc in tax_codes
        if isinstance(tc, dict) and str(tc.get("Id", "")).strip().isdigit()
    }


def _pick_default_purchase_tax_code_ref(tax_codes: List[Dict[str, Any]]) -> Dict[str, str] | None:
    env_id = os.getenv("QB_BILL_TAX_CODE_ID", "").strip()
    if env_id:
        name = ""
        for tc in tax_codes:
            if not isinstance(tc, dict):
                continue
            if str(tc.get("Id", "")).strip() == env_id:
                name = str(tc.get("Name") or "")
                break
        ref: Dict[str, str] = {"value": env_id}
        if name:
            ref["name"] = name
        return ref

    def _active(tc: Dict[str, Any]) -> bool:
        return tc.get("Active", True) is not False

    for tc in tax_codes:
        if not isinstance(tc, dict) or not _active(tc):
            continue
        tid = str(tc.get("Id", "")).strip()
        if not tid.isdigit():
            continue
        if tc.get("Taxable") is True:
            return {"value": tid, "name": str(tc.get("Name") or "")}

    for tc in tax_codes:
        if not isinstance(tc, dict) or not _active(tc):
            continue
        tid = str(tc.get("Id", "")).strip()
        if not tid.isdigit():
            continue
        ptr = tc.get("PurchaseTaxRateList")
        if isinstance(ptr, dict) and ptr.get("TaxRateDetail"):
            return {"value": tid, "name": str(tc.get("Name") or "")}

    for tc in tax_codes:
        if not isinstance(tc, dict) or not _active(tc):
            continue
        tid = str(tc.get("Id", "")).strip()
        if not tid.isdigit():
            continue
        name = str(tc.get("Name", "")).lower()
        if "exempt" in name or name in ("non", "non-taxable", "out of scope"):
            continue
        if "non" in name and "taxable" not in name:
            continue
        return {"value": tid, "name": str(tc.get("Name") or "")}

    return None


def _apply_purchase_tax_to_bill_lines(
    bill: Dict[str, Any],
    duplicate_check: Dict[str, Any],
    tax_codes: List[Dict[str, Any]],
    rationale: str,
) -> str:
    """
    When the email shows a positive tax amount, mark the bill as TaxExcluded and set a purchase TaxCodeRef
    on each expense line so QuickBooks applies company tax settings (no separate tax expense line).
    """
    lines = bill.get("Line") or []
    if not isinstance(lines, list) or not lines:
        return rationale

    tax_raw = duplicate_check.get("tax")
    if tax_raw is None:
        return rationale
    try:
        tax_amt = float(tax_raw)
    except (TypeError, ValueError):
        return rationale
    if tax_amt <= 0:
        return rationale

    ref = _pick_default_purchase_tax_code_ref(tax_codes)
    valid_ids = _tax_code_id_set(tax_codes)
    rid = str((ref or {}).get("value") or "").strip()
    env_override = bool(os.getenv("QB_BILL_TAX_CODE_ID", "").strip())
    if ref and rid and valid_ids and rid not in valid_ids and not env_override:
        ref = None
    if not ref:
        note = (
            "Email shows tax but no purchase TaxCode could be resolved; set QB_BILL_TAX_CODE_ID in .env "
            "or ensure TaxCode entities exist in QuickBooks. Lines left with TaxCodeRef NON."
        )
        return f"{rationale} {note}".strip() if rationale else note

    bill["GlobalTaxCalculation"] = "TaxExcluded"
    for line in lines:
        if not isinstance(line, dict):
            continue
        dt = line.get("DetailType")
        if dt == "AccountBasedExpenseLineDetail":
            det = line.get("AccountBasedExpenseLineDetail") or {}
            det["TaxCodeRef"] = dict(ref)
            line["AccountBasedExpenseLineDetail"] = det
        elif dt == "ItemBasedExpenseLineDetail":
            det = line.get("ItemBasedExpenseLineDetail") or {}
            det["TaxCodeRef"] = dict(ref)
            line["ItemBasedExpenseLineDetail"] = det
    return rationale


def _append_invoice_tax_line_from_hints(
    inv: Dict[str, Any],
    duplicate_check: Dict[str, Any],
    default_item: Dict[str, str],
) -> None:
    lines = list(inv.get("Line") or [])
    if not lines or _ref_id_missing(default_item):
        return
    tax_raw = duplicate_check.get("tax")
    total_raw = duplicate_check.get("total")
    sub_raw = duplicate_check.get("subtotal")
    if tax_raw is None or total_raw is None:
        return
    try:
        tax_amt = float(tax_raw)
        total_amt = float(total_raw)
    except (TypeError, ValueError):
        return
    if tax_amt <= 0 or total_amt <= 0:
        return
    if any(_description_suggests_tax_line(str((line or {}).get("Description") or "")) for line in lines):
        return
    line_sum = _line_sum_amounts(lines)
    if sub_raw is not None:
        try:
            sub_amt = float(sub_raw)
        except (TypeError, ValueError):
            sub_amt = line_sum
        if abs(line_sum - sub_amt) > 0.06:
            return
    if abs(line_sum + tax_amt - total_amt) > 0.06:
        return
    lines.append(_as_sales_item_line("Sales tax (from email)", tax_amt, default_item))
    inv["Line"] = lines


def build_bill_payload_from_email(
    email: Dict[str, Any],
    parsed: Dict[str, Any],
    *,
    items: List[Dict[str, Any]],
    vendors: List[Dict[str, Any]],
    accounts: List[Dict[str, Any]],
    tax_codes: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    body = get_email_body_text(email)
    bill: Dict[str, Any] = dict(parsed.get("bill") or {})
    duplicate_check: Dict[str, Any] = dict(parsed.get("duplicate_check") or {})
    rationale = str(parsed.get("rationale") or "").strip()
    tc_list = list(tax_codes or [])

    for key, val in _extract_amount_hints_from_body(body).items():
        if val is not None and duplicate_check.get(key) is None:
            duplicate_check[key] = val

    if duplicate_check.get("subtotal") is None:
        tr, tt = duplicate_check.get("tax"), duplicate_check.get("total")
        if tr is not None and tt is not None:
            try:
                tax_f = float(tr)
                tot_f = float(tt)
            except (TypeError, ValueError):
                tax_f, tot_f = 0.0, 0.0
            if tax_f > 0 and tot_f > tax_f + 0.01:
                duplicate_check["subtotal"] = round(tot_f - tax_f, 2)

    vr = _resolve_vendor_ref(vendors, body, bill.get("VendorRef"))
    if vr:
        bill["VendorRef"] = vr

    txn_raw, due_raw = _extract_bill_dates(body)
    txn_norm = normalize_quickbooks_date(txn_raw) or normalize_quickbooks_date(bill.get("TxnDate"))
    due_norm = normalize_quickbooks_date(due_raw) or normalize_quickbooks_date(bill.get("DueDate"))
    if txn_norm:
        bill["TxnDate"] = txn_norm
    if due_norm:
        bill["DueDate"] = due_norm

    if not bill.get("CurrencyRef"):
        bill["CurrencyRef"] = {"value": "USD"}

    account_ref = _default_account_ref(accounts)
    default_item = _default_bill_item_ref(items)
    body_rows = _extract_line_items_from_body(body)
    llm_lines = bill.get("Line") or []
    used_body_lines = bool(body_rows and not llm_lines)

    if used_body_lines:
        bill["Line"] = _lines_from_body_with_items(body_rows, account_ref, items, default_item)
        if not rationale:
            rationale = "Line items built from email body (LLM returned no lines)."
    elif llm_lines:
        if _prune_spurious_bill_expense_lines(bill):
            note = "Removed summary/tax/total/date rows mistaken for bill lines."
            rationale = f"{rationale} {note}".strip() if rationale else note

    if _strip_bill_tax_expense_lines(bill):
        note = "Removed tax-as-line-item rows; tax is applied via QuickBooks TaxCodeRef / GlobalTaxCalculation."
        rationale = f"{rationale} {note}".strip() if rationale else note
    _maybe_scale_bill_lines_to_subtotal(bill, duplicate_check)
    rationale = _apply_purchase_tax_to_bill_lines(bill, duplicate_check, tc_list, rationale)

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

    if _strip_open_balance_invoice_lines(inv):
        note = "Removed Open Balance / prior-balance rows (not current invoice line items)."
        rationale = f"{rationale} {note}".strip() if rationale else note

    if body_rows and not llm_lines:
        _append_invoice_tax_line_from_hints(inv, duplicate_check, default_item)

    inv = _drop_none_values(inv)
    return {"invoice": inv, "duplicate_check": duplicate_check, "rationale": rationale}

