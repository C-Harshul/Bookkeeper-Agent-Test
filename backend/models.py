from typing import Any, Dict, List, Literal, Optional, TypedDict

from pydantic import BaseModel, Field


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
    classification_mode: str
    forced_scenario: str
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
    workflow_failed: bool
    workflow_failure_reason: str

