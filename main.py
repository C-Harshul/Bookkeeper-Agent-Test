import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional, TypedDict

import requests
from dotenv import load_dotenv
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.output_parsers import PydanticOutputParser
from langchain_core.language_models import BaseChatModel
from langchain_community.chat_models import ChatOllama
from langgraph.graph import END, StateGraph
from pydantic import BaseModel, Field

try:
    from langchain_google_genai import ChatGoogleGenerativeAI
except ImportError:
    ChatGoogleGenerativeAI = None

load_dotenv()


class VendorRefModel(BaseModel):
    value: str
    name: Optional[str] = None


class CustomerRefModel(BaseModel):
    value: str
    name: Optional[str] = None


class AccountRefModel(BaseModel):
    value: str
    name: Optional[str] = None


class ItemRefModel(BaseModel):
    value: str
    name: Optional[str] = None


class CurrencyRefModel(BaseModel):
    value: str = "USD"


class TaxCodeRefModel(BaseModel):
    value: str = "NON"


class AccountBasedExpenseLineDetailModel(BaseModel):
    BillableStatus: str = "NotBillable"
    AccountRef: AccountRefModel
    TaxCodeRef: TaxCodeRefModel = Field(default_factory=TaxCodeRefModel)


class ItemBasedExpenseLineDetailModel(BaseModel):
    BillableStatus: str = "NotBillable"
    ItemRef: ItemRefModel
    UnitPrice: float
    Qty: float = 1
    TaxCodeRef: TaxCodeRefModel = Field(default_factory=TaxCodeRefModel)


class BillLine(BaseModel):
    Description: Optional[str] = None
    Amount: float
    DetailType: Literal["AccountBasedExpenseLineDetail", "ItemBasedExpenseLineDetail"]
    AccountBasedExpenseLineDetail: Optional[AccountBasedExpenseLineDetailModel] = None
    ItemBasedExpenseLineDetail: Optional[ItemBasedExpenseLineDetailModel] = None


class SalesItemLineDetail(BaseModel):
    ItemRef: ItemRefModel
    Qty: float = 1
    UnitPrice: float


class InvoiceLine(BaseModel):
    Amount: float
    DetailType: Literal["SalesItemLineDetail"] = "SalesItemLineDetail"
    SalesItemLineDetail: SalesItemLineDetail


class BillPayload(BaseModel):
    DueDate: Optional[str] = None
    VendorRef: Optional[VendorRefModel] = None
    APAccountRef: Optional[AccountRefModel] = None
    TxnDate: Optional[str] = None
    CurrencyRef: Optional[CurrencyRefModel] = Field(default_factory=CurrencyRefModel)
    Line: List[BillLine] = Field(default_factory=list)


class InvoicePayload(BaseModel):
    DocNumber: Optional[str] = None
    TxnDate: Optional[str] = None
    CustomerRef: Optional[CustomerRefModel] = None
    Line: List[InvoiceLine] = Field(default_factory=list)


class ClassificationOutput(BaseModel):
    action: Literal["bill", "invoice", "no_action"] = "no_action"
    rationale: Optional[str] = None


class BillAgentOutput(BaseModel):
    bill: BillPayload = Field(default_factory=BillPayload)
    duplicate_check: Dict[str, Any] = Field(default_factory=dict)
    rationale: Optional[str] = None


class InvoiceAgentOutput(BaseModel):
    invoice: InvoicePayload = Field(default_factory=InvoicePayload)
    duplicate_check: Dict[str, Any] = Field(default_factory=dict)
    rationale: Optional[str] = None


class GraphState(TypedDict, total=False):
    email: Dict[str, Any]
    action: str
    rationale: str
    bills: List[Dict[str, Any]]
    invoices: List[Dict[str, Any]]
    items: List[Dict[str, Any]]
    customers: List[Dict[str, Any]]
    vendors: List[Dict[str, Any]]
    accounts: List[Dict[str, Any]]
    parsed_bill: Dict[str, Any]
    parsed_invoice: Dict[str, Any]
    duplicate_found: bool
    result: Dict[str, Any]


def log_step(step: str, message: str) -> None:
    print(f"[STEP:{step}] {message}")


def _contains_address(haystack: str, needle: str) -> bool:
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
    return any(_contains_address(str(value), target_email) for value in fields)


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


class QuickBooksClient:
    def __init__(self, realm_id: str, access_token: str, minor_version: str = "69"):
        self.realm_id = realm_id
        self.access_token = access_token
        self.minor_version = minor_version
        self.base = f"https://sandbox-quickbooks.api.intuit.com/v3/company/{realm_id}"

    @property
    def headers(self) -> Dict[str, str]:
        return {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.access_token}",
        }

    def _get(self, resource: str) -> Dict[str, Any]:
        url = f"{self.base}/{resource}"
        response = requests.get(
            url,
            params={"minorversion": self.minor_version},
            headers=self.headers,
            timeout=30,
        )
        response.raise_for_status()
        return response.json()

    def _query(self, sql: str) -> Dict[str, Any]:
        url = f"{self.base}/query"
        response = requests.get(
            url,
            params={"minorversion": self.minor_version, "query": sql},
            headers=self.headers,
            timeout=30,
        )
        response.raise_for_status()
        return response.json()

    def _post(self, resource: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        url = f"{self.base}/{resource}"
        response = requests.post(
            url,
            params={"minorversion": self.minor_version},
            headers=self.headers,
            json=payload,
            timeout=30,
        )
        try:
            response.raise_for_status()
        except requests.HTTPError as exc:
            detail = response.text[:2000]
            raise requests.HTTPError(
                f"{exc}. QuickBooks response: {detail}",
                response=response,
            ) from exc
        return response.json()

    def get_bills(self) -> List[Dict[str, Any]]:
        data = self._query("select * from Bill")
        return data.get("QueryResponse", {}).get("Bill", [])

    def get_invoices(self) -> List[Dict[str, Any]]:
        data = self._query("select * from Invoice")
        return data.get("QueryResponse", {}).get("Invoice", [])

    def get_items(self) -> List[Dict[str, Any]]:
        data = self._query("select * from Item")
        return data.get("QueryResponse", {}).get("Item", [])

    def get_customers(self) -> List[Dict[str, Any]]:
        data = self._query("select * from Customer")
        return data.get("QueryResponse", {}).get("Customer", [])

    def get_vendors(self) -> List[Dict[str, Any]]:
        data = self._query("select * from Vendor")
        return data.get("QueryResponse", {}).get("Vendor", [])

    def get_accounts(self) -> List[Dict[str, Any]]:
        data = self._query("select * from Account")
        return data.get("QueryResponse", {}).get("Account", [])

    def create_bill(self, bill_payload: Dict[str, Any]) -> Dict[str, Any]:
        return self._post("bill", bill_payload)

    def create_invoice(self, invoice_payload: Dict[str, Any]) -> Dict[str, Any]:
        return self._post("invoice", invoice_payload)


def make_llm() -> BaseChatModel:
    provider = os.getenv("LLM_PROVIDER", "ollama").strip().lower()
    temperature = float(os.getenv("LLM_TEMPERATURE", "0"))

    if provider == "gemini":
        if ChatGoogleGenerativeAI is None:
            raise ImportError(
                "langchain-google-genai is not installed. Install dependencies from requirements.txt."
            )
        model = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
        api_key = os.getenv("GOOGLE_API_KEY")
        if not api_key:
            raise ValueError("GOOGLE_API_KEY is required when LLM_PROVIDER=gemini")
        return ChatGoogleGenerativeAI(model=model, temperature=temperature, google_api_key=api_key)

    model = os.getenv("OLLAMA_MODEL", "qwen2.5:7b")
    base_url = os.getenv("OLLAMA_BASE_URL")
    kwargs: Dict[str, Any] = {"model": model, "temperature": temperature}
    if base_url:
        kwargs["base_url"] = base_url
    return ChatOllama(**kwargs)


def classify_email_node(state: GraphState) -> GraphState:
    log_step("classify_email", "Classifying incoming email as bill/invoice/no_action")
    parser = PydanticOutputParser(pydantic_object=ClassificationOutput)
    llm = make_llm()

    email = state["email"]
    email_blob = {
        "subject": email.get("subject", ""),
        "from": email.get("from", ""),
        "date": email.get("date", ""),
        "html": email.get("html", ""),
        "text": email.get("text", ""),
    }
    response = llm.invoke(
        [
            SystemMessage(
                content=(
                    "You classify accounting emails.\n"
                    "Return action in {'bill','invoice','no_action'} only.\n"
                    "Use no_action for unrelated emails.\n"
                    f"{parser.get_format_instructions()}"
                )
            ),
            HumanMessage(content=json.dumps({"email": email_blob}, default=str)),
        ]
    )
    classified = parser.parse(response.content)
    log_step("classify_email", f"Classified action={classified.action}")
    return {
        **state,
        "action": classified.action,
        "rationale": classified.rationale or "",
    }


def fetch_bill_context_node(state: GraphState) -> GraphState:
    log_step("fetch_bill_context", "Fetching vendors, items, accounts for bill processing")
    qb = QuickBooksClient(
        realm_id=os.environ["QB_REALM_ID"],
        access_token=os.environ["QB_ACCESS_TOKEN"],
        minor_version=os.getenv("QB_MINOR_VERSION", "69"),
    )
    next_state = {
        **state,
        "items": qb.get_items(),
        "vendors": qb.get_vendors(),
        "accounts": qb.get_accounts(),
    }
    log_step(
        "fetch_bill_context",
        "Loaded bill context: "
        f"items={len(next_state['items'])}, "
        f"vendors={len(next_state['vendors'])}, "
        f"accounts={len(next_state['accounts'])}",
    )
    return next_state


def fetch_invoice_context_node(state: GraphState) -> GraphState:
    log_step("fetch_invoice_context", "Fetching customers and items for invoice processing")
    qb = QuickBooksClient(
        realm_id=os.environ["QB_REALM_ID"],
        access_token=os.environ["QB_ACCESS_TOKEN"],
        minor_version=os.getenv("QB_MINOR_VERSION", "69"),
    )
    next_state = {
        **state,
        "items": qb.get_items(),
        "customers": qb.get_customers(),
    }
    log_step(
        "fetch_invoice_context",
        "Loaded invoice context: "
        f"items={len(next_state['items'])}, "
        f"customers={len(next_state['customers'])}",
    )
    return next_state


def parse_bill_node(state: GraphState) -> GraphState:
    log_step("parse_bill", "Running LLM bill extraction")
    parser = PydanticOutputParser(pydantic_object=BillAgentOutput)
    llm = make_llm()

    email = state["email"]
    email_blob = {
        "subject": email.get("subject", ""),
        "from": email.get("from", ""),
        "date": email.get("date", ""),
        "html": email.get("html", ""),
        "text": email.get("text", ""),
    }

    system_prompt = (
        "You are an Accounting AI Agent for BILL extraction only.\n"
        "Rules:\n"
        "1) Return only Bill fields in the schema.\n"
        "2) For bill line details: if matching item has expense account use "
        "ItemBasedExpenseLineDetail, otherwise use AccountBasedExpenseLineDetail.\n"
        "3) Use VendorRef from provided vendors.\n"
        "4) Include duplicate_check hints with keys helpful for comparison.\n"
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

    response = llm.invoke(
        [
            SystemMessage(content=system_prompt),
            HumanMessage(content=json.dumps(user_prompt, default=str)),
        ]
    )
    parsed = parser.parse(response.content)
    log_step("parse_bill", "Bill payload parsed")

    return {
        **state,
        "parsed_bill": parsed.model_dump(),
    }


def parse_invoice_node(state: GraphState) -> GraphState:
    log_step("parse_invoice", "Running LLM invoice extraction")
    parser = PydanticOutputParser(pydantic_object=InvoiceAgentOutput)
    llm = make_llm()

    email = state["email"]
    email_blob = {
        "subject": email.get("subject", ""),
        "from": email.get("from", ""),
        "date": email.get("date", ""),
        "html": email.get("html", ""),
        "text": email.get("text", ""),
    }

    system_prompt = (
        "You are an Accounting AI Agent for INVOICE extraction only.\n"
        "Rules:\n"
        "1) Return only Invoice fields in the schema.\n"
        "2) Use CustomerRef from provided customers.\n"
        "3) Use SalesItemLineDetail.ItemRef from provided items.\n"
        "4) Include duplicate_check hints with keys helpful for comparison.\n"
        f"{parser.get_format_instructions()}"
    )

    user_prompt = {
        "email": email_blob,
        "reference": {
            "items": state.get("items", []),
            "customers": state.get("customers", []),
        },
    }
    response = llm.invoke(
        [
            SystemMessage(content=system_prompt),
            HumanMessage(content=json.dumps(user_prompt, default=str)),
        ]
    )
    parsed = parser.parse(response.content)
    log_step("parse_invoice", "Invoice payload parsed")
    return {**state, "parsed_invoice": parsed.model_dump()}


def fetch_existing_bills_node(state: GraphState) -> GraphState:
    log_step("fetch_existing_bills", "Fetching existing bills for duplicate check")
    qb = QuickBooksClient(
        realm_id=os.environ["QB_REALM_ID"],
        access_token=os.environ["QB_ACCESS_TOKEN"],
        minor_version=os.getenv("QB_MINOR_VERSION", "69"),
    )
    bills = qb.get_bills()
    log_step("fetch_existing_bills", f"Fetched bills={len(bills)}")
    return {**state, "bills": bills}


def fetch_existing_invoices_node(state: GraphState) -> GraphState:
    log_step("fetch_existing_invoices", "Fetching existing invoices for duplicate check")
    qb = QuickBooksClient(
        realm_id=os.environ["QB_REALM_ID"],
        access_token=os.environ["QB_ACCESS_TOKEN"],
        minor_version=os.getenv("QB_MINOR_VERSION", "69"),
    )
    invoices = qb.get_invoices()
    log_step("fetch_existing_invoices", f"Fetched invoices={len(invoices)}")
    return {**state, "invoices": invoices}


def check_bill_duplicate_node(state: GraphState) -> GraphState:
    log_step("check_bill_duplicate", "Checking duplicate bill")
    bill = state.get("parsed_bill", {}).get("bill", {})
    vendor = (bill.get("VendorRef") or {}).get("value")
    txn_date = bill.get("TxnDate")
    amount = sum((line or {}).get("Amount", 0) for line in bill.get("Line", []))
    duplicate = False
    for existing in state.get("bills", []):
        same_vendor = ((existing.get("VendorRef") or {}).get("value") == vendor)
        same_date = existing.get("TxnDate") == txn_date
        existing_total = existing.get("TotalAmt")
        same_amount = existing_total is not None and abs(float(existing_total) - float(amount)) < 0.01
        if same_vendor and same_date and same_amount:
            duplicate = True
            break
    log_step("check_bill_duplicate", f"Duplicate found={duplicate}")
    return {**state, "duplicate_found": duplicate}


def check_invoice_duplicate_node(state: GraphState) -> GraphState:
    log_step("check_invoice_duplicate", "Checking duplicate invoice")
    invoice = state.get("parsed_invoice", {}).get("invoice", {})
    doc = invoice.get("DocNumber")
    customer = (invoice.get("CustomerRef") or {}).get("value")
    amount = sum((line or {}).get("Amount", 0) for line in invoice.get("Line", []))
    duplicate = False
    for existing in state.get("invoices", []):
        same_doc = existing.get("DocNumber") == doc if doc else False
        same_customer = ((existing.get("CustomerRef") or {}).get("value") == customer)
        existing_total = existing.get("TotalAmt")
        same_amount = existing_total is not None and abs(float(existing_total) - float(amount)) < 0.01
        if same_doc or (same_customer and same_amount):
            duplicate = True
            break
    log_step("check_invoice_duplicate", f"Duplicate found={duplicate}")
    return {**state, "duplicate_found": duplicate}


def route_from_classification(state: GraphState) -> str:
    action = state.get("action", "no_action")
    if action == "bill":
        log_step("route_classification", "Routing to bill branch")
        return "bill"
    if action == "invoice":
        log_step("route_classification", "Routing to invoice branch")
        return "invoice"
    log_step("route_classification", "Routing to no_action")
    return "no_action"


def route_after_duplicate(state: GraphState) -> str:
    if state.get("duplicate_found"):
        return "no_action"
    return "create"


def create_bill_node(state: GraphState) -> GraphState:
    log_step("create_bill", "Creating Bill in QuickBooks")
    qb = QuickBooksClient(
        realm_id=os.environ["QB_REALM_ID"],
        access_token=os.environ["QB_ACCESS_TOKEN"],
        minor_version=os.getenv("QB_MINOR_VERSION", "69"),
    )
    payload = state["parsed_bill"]["bill"]
    accounts = state.get("accounts", [])

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

    result = qb.create_bill(payload)
    log_step("create_bill", f"Bill created successfully. Response keys={list(result.keys())}")
    return {**state, "result": {"action": "bill_created", "response": result}}


def create_invoice_node(state: GraphState) -> GraphState:
    log_step("create_invoice", "Creating Invoice in QuickBooks")
    qb = QuickBooksClient(
        realm_id=os.environ["QB_REALM_ID"],
        access_token=os.environ["QB_ACCESS_TOKEN"],
        minor_version=os.getenv("QB_MINOR_VERSION", "69"),
    )
    payload = state["parsed_invoice"]["invoice"]
    result = qb.create_invoice(payload)
    log_step("create_invoice", f"Invoice created successfully. Response keys={list(result.keys())}")
    return {**state, "result": {"action": "invoice_created", "response": result}}


def no_action_node(state: GraphState) -> GraphState:
    parsed = state.get("parsed_bill") or state.get("parsed_invoice") or {}
    reason = "duplicate" if state.get("duplicate_found") else state.get("rationale", "no_action")
    log_step("no_action", f"No action taken. Reason={reason}")
    return {**state, "result": {"action": "no_action", "reason": reason, "parsed": parsed}}


def build_graph():
    graph = StateGraph(GraphState)
    graph.add_node("inspect_email", inspect_email_node)
    graph.add_node("classify_email", classify_email_node)
    graph.add_node("fetch_bill_context", fetch_bill_context_node)
    graph.add_node("parse_bill", parse_bill_node)
    graph.add_node("fetch_existing_bills", fetch_existing_bills_node)
    graph.add_node("check_bill_duplicate", check_bill_duplicate_node)
    graph.add_node("fetch_invoice_context", fetch_invoice_context_node)
    graph.add_node("parse_invoice", parse_invoice_node)
    graph.add_node("fetch_existing_invoices", fetch_existing_invoices_node)
    graph.add_node("check_invoice_duplicate", check_invoice_duplicate_node)
    graph.add_node("create_bill", create_bill_node)
    graph.add_node("create_invoice", create_invoice_node)
    graph.add_node("no_action", no_action_node)

    graph.set_entry_point("inspect_email")
    graph.add_conditional_edges(
        "inspect_email",
        lambda state: "stop" if state.get("result", {}).get("action") == "no_action" else "continue",
        {"stop": "no_action", "continue": "classify_email"},
    )
    graph.add_conditional_edges(
        "classify_email",
        route_from_classification,
        {
            "bill": "fetch_bill_context",
            "invoice": "fetch_invoice_context",
            "no_action": "no_action",
        },
    )
    graph.add_edge("fetch_bill_context", "parse_bill")
    graph.add_edge("parse_bill", "fetch_existing_bills")
    graph.add_edge("fetch_existing_bills", "check_bill_duplicate")
    graph.add_conditional_edges(
        "check_bill_duplicate",
        route_after_duplicate,
        {"create": "create_bill", "no_action": "no_action"},
    )

    graph.add_edge("fetch_invoice_context", "parse_invoice")
    graph.add_edge("parse_invoice", "fetch_existing_invoices")
    graph.add_edge("fetch_existing_invoices", "check_invoice_duplicate")
    graph.add_conditional_edges(
        "check_invoice_duplicate",
        route_after_duplicate,
        {"create": "create_invoice", "no_action": "no_action"},
    )
    graph.add_edge("create_bill", END)
    graph.add_edge("create_invoice", END)
    graph.add_edge("no_action", END)
    return graph.compile()


def run_once(email_payload: Dict[str, Any]) -> Dict[str, Any]:
    log_step("run_once", "Starting workflow execution")
    app = build_graph()
    output = app.invoke({"email": email_payload})
    result = output.get("result", {"action": "no_action", "reason": "empty_result"})
    log_step("run_once", f"Workflow finished with action={result.get('action')}")
    return result


if __name__ == "__main__":
    # Demo entrypoint. Replace with a Gmail poller / webhook handler as needed.
    sample = {
        "subject": "Bill from Brosnahan Insurance Agency",
        "from": "numinatest2@gmail.com",
        "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "html": "",
        "text": (
            "Hello,\n"
            "Please record the following bill from Brosnahan Insurance Agency:\n"
            "Items Purchased:\n"
            "- Liability Insurance (12 months @ $130 per month) - $1,560.00\n"
            "- Commercial Property Insurance - $900.00\n"
            "- Workers' Compensation Insurance - $500.00\n"
            "Subtotal: $2,960.00\n"
            "Tax (10%): $296.00\n"
            "Total: $3,256.00\n"
            "Payment Method: Credit Card\n"
            "Bill Date: April 1, 2026\n"
            "Due Date: April 20, 2026\n"
            "Category: Insurance\n"
            "Vendor: Brosnahan Insurance Agency\n"
            "Regards,\n"
            "Finance Team"
        ),
    }
    print(json.dumps(run_once(sample), indent=2))
