export type NodeCategory = 'trigger' | 'ai' | 'data-fetch' | 'action' | 'router' | 'terminal';

export type NodeStatus = 'idle' | 'running' | 'done' | 'failed';
/** Bill / invoice / no_action when forcing a path without LLM classification. */
export type WorkflowScenario = 'bill' | 'invoice' | 'no_action';

/** Toolbar run mode: auto uses LLM classify; others skip LLM and force that branch. */
export type WorkflowRunMode = 'auto' | WorkflowScenario;

export interface NodeExecutionLog {
  timestamp: string;
  level: 'INFO' | 'DEBUG' | 'SUCCESS' | 'ERROR';
  message: string;
}

export interface NodeCategoryConfig {
  name: string;
  color: string;
  icon: string;
}

export const NODE_CATEGORIES: Record<NodeCategory, NodeCategoryConfig> = {
  trigger: {
    name: 'Trigger',
    color: '#3b82f6',
    icon: '🔵',
  },
  ai: {
    name: 'AI / LLM',
    color: '#8b5cf6',
    icon: '🟣',
  },
  'data-fetch': {
    name: 'Data Fetch',
    color: '#f97316',
    icon: '🟠',
  },
  action: {
    name: 'Action',
    color: '#22c55e',
    icon: '🟢',
  },
  router: {
    name: 'Router',
    color: '#64748b',
    icon: '⚫',
  },
  terminal: {
    name: 'Terminal',
    color: '#ef4444',
    icon: '🔴',
  },
};

export interface WorkflowNodeData {
  label: string;
  icon: string;
  subtitle: string;
  category: NodeCategory;
  status: NodeStatus;
  requiresOAuth?: boolean;
  oauthEnabled?: boolean;
  description?: string;
  config?: Record<string, string>;
}

export interface NodeLogicDetail {
  summary: string;
  pythonNode: string;
  inputs: string[];
  outputs: string[];
  logic: string[];
}

export interface NodeTypeDefinition {
  id: string;
  type: string;
  category: NodeCategory;
  label: string;
  icon: string;
  subtitle: string;
}

export const NODE_TYPE_DEFINITIONS: NodeTypeDefinition[] = [
  {
    id: 'inspect_email',
    type: 'custom',
    category: 'trigger',
    label: 'Inspect Email',
    icon: '✉️',
    subtitle: 'Reads & filters incoming email payload',
  },
  {
    id: 'classify_email',
    type: 'custom',
    category: 'ai',
    label: 'Classify Email',
    icon: '🤖',
    subtitle: 'LLM classifies email as bill / invoice / no_action',
  },
  {
    id: 'fetch_bill_context',
    type: 'custom',
    category: 'data-fetch',
    label: 'Fetch Bill Context',
    icon: '🗂️',
    subtitle: 'Fetches vendors, items & accounts from QuickBooks',
  },
  {
    id: 'parse_bill',
    type: 'custom',
    category: 'ai',
    label: 'Parse Bill',
    icon: '🤖',
    subtitle: 'LLM extracts bill payload from email content',
  },
  {
    id: 'fetch_existing_bills',
    type: 'custom',
    category: 'data-fetch',
    label: 'Fetch Existing Bills',
    icon: '📋',
    subtitle: 'Retrieves existing bills from QuickBooks',
  },
  {
    id: 'check_bill_duplicate',
    type: 'custom',
    category: 'router',
    label: 'Check Bill Duplicate',
    icon: '🔍',
    subtitle: 'Checks vendor, date & amount for duplicates',
  },
  {
    id: 'fetch_invoice_context',
    type: 'custom',
    category: 'data-fetch',
    label: 'Fetch Invoice Context',
    icon: '🗂️',
    subtitle: 'Fetches customers & items from QuickBooks',
  },
  {
    id: 'parse_invoice',
    type: 'custom',
    category: 'ai',
    label: 'Parse Invoice',
    icon: '🤖',
    subtitle: 'LLM extracts invoice payload from email content',
  },
  {
    id: 'fetch_existing_invoices',
    type: 'custom',
    category: 'data-fetch',
    label: 'Fetch Existing Invoices',
    icon: '📋',
    subtitle: 'Retrieves existing invoices from QuickBooks',
  },
  {
    id: 'check_invoice_duplicate',
    type: 'custom',
    category: 'router',
    label: 'Check Invoice Duplicate',
    icon: '🔍',
    subtitle: 'Checks doc number, customer & amount for duplicates',
  },
  {
    id: 'create_bill',
    type: 'custom',
    category: 'action',
    label: 'Create Bill',
    icon: '✅',
    subtitle: 'POSTs new bill to QuickBooks API',
  },
  {
    id: 'create_invoice',
    type: 'custom',
    category: 'action',
    label: 'Create Invoice',
    icon: '✅',
    subtitle: 'POSTs new invoice to QuickBooks API',
  },
  {
    id: 'no_action',
    type: 'custom',
    category: 'terminal',
    label: 'No Action',
    icon: '🚫',
    subtitle: 'Workflow ends — duplicate found or unrelated email',
  },
];

export const NODE_LOGIC_DETAILS: Record<string, NodeLogicDetail> = {
  inspect_email: {
    summary: 'Print email metadata/body and stop early if sender/content does not match tracking filter.',
    pythonNode: 'inspect_email_node',
    inputs: ['email payload', 'TRACK_EMAIL env var'],
    outputs: ['state.result=no_action when not tracked', 'pass-through state when tracked'],
    logic: [
      'Print From, Subject, Date, Body in terminal.',
      'Match target address against from/to/cc/bcc/subject/text/html.',
      'If unmatched, set action=no_action and reason=email_not_tracked.',
    ],
  },
  classify_email: {
    summary: 'Use LLM to classify email as bill, invoice, or no_action.',
    pythonNode: 'classify_email_node',
    inputs: ['email subject/body', 'LLM provider config'],
    outputs: ['state.action', 'state.rationale'],
    logic: [
      'Build classification-only prompt.',
      'Parse structured output with Pydantic.',
      'Route workflow based on classified action.',
    ],
  },
  fetch_bill_context: {
    summary: 'Fetch QuickBooks entities needed for bill extraction.',
    pythonNode: 'fetch_bill_context_node',
    inputs: ['QB_REALM_ID', 'QB_ACCESS_TOKEN', 'QB_MINOR_VERSION'],
    outputs: ['vendors', 'items', 'accounts'],
    logic: [
      'Query Vendor list.',
      'Query Item list.',
      'Query Account list.',
    ],
  },
  parse_bill: {
    summary: 'Use LLM to build structured QuickBooks bill payload.',
    pythonNode: 'parse_bill_node',
    inputs: ['email payload', 'vendors', 'items', 'accounts'],
    outputs: ['state.parsed_bill.bill', 'duplicate_check hints'],
    logic: [
      'Constrain response to Bill schema.',
      'Use VendorRef from fetched vendors.',
      'Choose item-based or account-based detail per line.',
    ],
  },
  fetch_existing_bills: {
    summary: 'Retrieve existing bills for duplicate detection.',
    pythonNode: 'fetch_existing_bills_node',
    inputs: ['QuickBooks API credentials'],
    outputs: ['state.bills'],
    logic: ['Run QuickBooks query: select * from Bill.'],
  },
  check_bill_duplicate: {
    summary: 'Detect duplicate bill by vendor/date/amount.',
    pythonNode: 'check_bill_duplicate_node',
    inputs: ['parsed bill', 'existing bills'],
    outputs: ['state.duplicate_found'],
    logic: [
      'Compare VendorRef.value.',
      'Compare TxnDate.',
      'Compare total amount (line sum vs TotalAmt).',
    ],
  },
  create_bill: {
    summary: 'POST validated bill payload to QuickBooks.',
    pythonNode: 'create_bill_node',
    inputs: ['parsed bill payload', 'accounts fallback'],
    outputs: ['state.result.action=bill_created', 'QuickBooks response'],
    logic: [
      'Sanitize invalid AccountRef IDs with default expense account.',
      'Send POST /bill.',
      'Return API response in result.',
    ],
  },
  fetch_invoice_context: {
    summary: 'Fetch QuickBooks entities needed for invoice extraction.',
    pythonNode: 'fetch_invoice_context_node',
    inputs: ['QuickBooks API credentials'],
    outputs: ['customers', 'items'],
    logic: [
      'Query Customer list.',
      'Query Item list.',
    ],
  },
  parse_invoice: {
    summary: 'Use LLM to build structured QuickBooks invoice payload.',
    pythonNode: 'parse_invoice_node',
    inputs: ['email payload', 'customers', 'items'],
    outputs: ['state.parsed_invoice.invoice', 'duplicate_check hints'],
    logic: [
      'Constrain response to Invoice schema.',
      'Resolve CustomerRef from customers list.',
      'Resolve SalesItemLineDetail.ItemRef from items list.',
    ],
  },
  fetch_existing_invoices: {
    summary: 'Retrieve existing invoices for duplicate detection.',
    pythonNode: 'fetch_existing_invoices_node',
    inputs: ['QuickBooks API credentials'],
    outputs: ['state.invoices'],
    logic: ['Run QuickBooks query: select * from Invoice.'],
  },
  check_invoice_duplicate: {
    summary: 'Detect duplicate invoice by doc number or customer/amount.',
    pythonNode: 'check_invoice_duplicate_node',
    inputs: ['parsed invoice', 'existing invoices'],
    outputs: ['state.duplicate_found'],
    logic: [
      'Check DocNumber equality when provided.',
      'Fallback check: CustomerRef + TotalAmt match.',
    ],
  },
  create_invoice: {
    summary: 'POST validated invoice payload to QuickBooks.',
    pythonNode: 'create_invoice_node',
    inputs: ['parsed invoice payload'],
    outputs: ['state.result.action=invoice_created', 'QuickBooks response'],
    logic: [
      'Send POST /invoice.',
      'Return API response in result.',
    ],
  },
  no_action: {
    summary: 'Terminal node when email is irrelevant or duplicate found.',
    pythonNode: 'no_action_node',
    inputs: ['duplicate flag / classification rationale'],
    outputs: ['state.result.action=no_action'],
    logic: [
      'Set reason=duplicate when duplicate flag is true.',
      'Otherwise set reason to classification rationale.',
    ],
  },
};
