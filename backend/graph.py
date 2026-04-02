from langgraph.graph import END, StateGraph

from backend.models import GraphState
from backend.nodes import (
    check_bill_duplicate_node,
    check_invoice_duplicate_node,
    classify_email_node,
    create_bill_node,
    create_invoice_node,
    fetch_bill_context_node,
    fetch_existing_bills_node,
    fetch_existing_invoices_node,
    fetch_invoice_context_node,
    inspect_email_node,
    no_action_node,
    parse_bill_node,
    parse_invoice_node,
)


def route_from_classification(state: GraphState) -> str:
    action = state.get("action", "no_action")
    if action == "bill":
        return "bill"
    if action == "invoice":
        return "invoice"
    return "no_action"


def route_after_duplicate(state: GraphState) -> str:
    if state.get("duplicate_found"):
        return "no_action"
    return "create"


NODE_REGISTRY = {
    "inspect_email": inspect_email_node,
    "classify_email": classify_email_node,
    "fetch_bill_context": fetch_bill_context_node,
    "parse_bill": parse_bill_node,
    "fetch_existing_bills": fetch_existing_bills_node,
    "check_bill_duplicate": check_bill_duplicate_node,
    "fetch_invoice_context": fetch_invoice_context_node,
    "parse_invoice": parse_invoice_node,
    "fetch_existing_invoices": fetch_existing_invoices_node,
    "check_invoice_duplicate": check_invoice_duplicate_node,
    "create_bill": create_bill_node,
    "create_invoice": create_invoice_node,
    "no_action": no_action_node,
}


def register_nodes(graph: StateGraph) -> None:
    for node_name, node_fn in NODE_REGISTRY.items():
        graph.add_node(node_name, node_fn)


def setup_graph_edges(graph: StateGraph) -> None:
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


def build_graph():
    graph = StateGraph(GraphState)
    register_nodes(graph)
    setup_graph_edges(graph)
    return graph.compile()

