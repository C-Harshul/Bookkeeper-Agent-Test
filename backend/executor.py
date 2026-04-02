from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Iterator, List, Optional, Tuple

from backend.graph import NODE_REGISTRY
from backend.models import GraphState


def _timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%H:%M:%S")


def _base_node_id(node_id: str) -> str:
    # Supports dynamically created ids like "parse_bill_1712345678"
    for known in NODE_REGISTRY:
        if node_id == known or node_id.startswith(f"{known}_"):
            return known
    return node_id


def _build_adjacency(edges: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    adjacency: Dict[str, List[Dict[str, Any]]] = {}
    for edge in edges:
        source = str(edge.get("source", ""))
        adjacency.setdefault(source, []).append(edge)
    return adjacency


def _pick_start_node(nodes: List[Dict[str, Any]], edges: List[Dict[str, Any]]) -> Optional[str]:
    incoming_count: Dict[str, int] = {str(node.get("id", "")): 0 for node in nodes}
    for edge in edges:
        target = str(edge.get("target", ""))
        if target in incoming_count:
            incoming_count[target] += 1

    # Prefer inspect_email if present.
    for node in nodes:
        node_id = str(node.get("id", ""))
        if _base_node_id(node_id) == "inspect_email":
            return node_id

    roots = [node_id for node_id, count in incoming_count.items() if count == 0]
    return roots[0] if roots else (str(nodes[0].get("id")) if nodes else None)


def _edge_label(edge: Dict[str, Any]) -> str:
    return str(edge.get("label") or "").strip().lower()


def _choose_next_edge(current: str, outgoing: List[Dict[str, Any]], state: GraphState) -> Optional[Dict[str, Any]]:
    if not outgoing:
        return None
    if len(outgoing) == 1:
        return outgoing[0]

    base = _base_node_id(current)
    labels = [(_edge_label(edge), edge) for edge in outgoing]

    if base == "classify_email":
        action = str(state.get("action", "no_action")).lower()
        for label, edge in labels:
            if label == action:
                return edge

    if base == "inspect_email":
        no_action = state.get("result", {}).get("action") == "no_action"
        if no_action:
            for label, edge in labels:
                if label in {"no match", "stop", "no_action"}:
                    return edge
        else:
            for label, edge in labels:
                if label in {"matched", "continue", ""}:
                    return edge

    if base in {"check_bill_duplicate", "check_invoice_duplicate"}:
        duplicate = bool(state.get("duplicate_found"))
        if duplicate:
            for label, edge in labels:
                if label in {"duplicate", "no_action"}:
                    return edge
        else:
            for label, edge in labels:
                if label in {"create", "continue", ""}:
                    return edge

    return sorted(outgoing, key=lambda edge: str(edge.get("target", "")))[0]


def _trim_output(value: Any, max_chars: int = 3000) -> Any:
    text = str(value)
    if len(text) <= max_chars:
        return value
    return f"{text[:max_chars]}...<truncated>"


def _extract_node_output(base: str, state: GraphState) -> Dict[str, Any]:
    if base == "inspect_email":
        return {"email": state.get("email", {})}
    if base == "classify_email":
        return {"action": state.get("action"), "rationale": state.get("rationale")}
    if base == "fetch_bill_context":
        return {
            "vendors_count": len(state.get("vendors", [])),
            "items_count": len(state.get("items", [])),
            "accounts_count": len(state.get("accounts", [])),
            "vendors_sample": state.get("vendors", [])[:2],
            "items_sample": state.get("items", [])[:2],
        }
    if base == "fetch_invoice_context":
        return {
            "customers_count": len(state.get("customers", [])),
            "items_count": len(state.get("items", [])),
            "customers_sample": state.get("customers", [])[:2],
            "items_sample": state.get("items", [])[:2],
        }
    if base == "parse_bill":
        return {"parsed_bill": state.get("parsed_bill", {})}
    if base == "parse_invoice":
        return {"parsed_invoice": state.get("parsed_invoice", {})}
    if base == "fetch_existing_bills":
        bills = state.get("bills", [])
        return {"bills_count": len(bills), "bills_sample": bills[:2]}
    if base == "fetch_existing_invoices":
        invoices = state.get("invoices", [])
        return {"invoices_count": len(invoices), "invoices_sample": invoices[:2]}
    if base in {"check_bill_duplicate", "check_invoice_duplicate"}:
        return {"duplicate_found": state.get("duplicate_found", False)}
    if base in {"create_bill", "create_invoice", "no_action"}:
        return {"result": state.get("result", {})}
    return {}


def execute_workflow_from_graph(
    *,
    nodes: List[Dict[str, Any]],
    edges: List[Dict[str, Any]],
    initial_state: GraphState,
    entry_node_id: Optional[str] = None,
    max_steps: int = 200,
) -> Tuple[GraphState, List[Dict[str, str]], Dict[str, List[Dict[str, str]]]]:
    state: GraphState = dict(initial_state)
    execution_order: List[Dict[str, str]] = []
    node_logs: Dict[str, List[Dict[str, str]]] = {}

    adjacency = _build_adjacency(edges)
    current = entry_node_id or _pick_start_node(nodes, edges)

    step_count = 0
    while current and step_count < max_steps:
        step_count += 1
        base = _base_node_id(current)
        handler = NODE_REGISTRY.get(base)
        if handler is None:
            # Unknown nodes are treated as pass-through custom placeholders.
            entry = {"timestamp": _timestamp(), "level": "DEBUG", "message": f"Skipped unknown node: {current}"}
            node_logs.setdefault(current, []).append(entry)
            execution_order.append({"nodeId": current, "status": "skipped"})
        else:
            start = {"timestamp": _timestamp(), "level": "INFO", "message": f"Executing {base}"}
            node_logs.setdefault(current, []).append(start)
            state = handler(state)
            done = {"timestamp": _timestamp(), "level": "SUCCESS", "message": f"Completed {base}"}
            node_logs.setdefault(current, []).append(done)
            execution_order.append({"nodeId": current, "status": "done"})

        outgoing = adjacency.get(current, [])
        next_edge = _choose_next_edge(current, outgoing, state)
        current = str(next_edge.get("target")) if next_edge else None

        # Terminal condition if result already produced and there's no meaningful next branch.
        if state.get("result", {}).get("action") and _base_node_id(current or "") in {"", "no_action"} and not outgoing:
            break

    if step_count >= max_steps:
        execution_order.append({"nodeId": "__system__", "status": "max_steps_reached"})

    return state, execution_order, node_logs


def stream_workflow_from_graph(
    *,
    nodes: List[Dict[str, Any]],
    edges: List[Dict[str, Any]],
    initial_state: GraphState,
    entry_node_id: Optional[str] = None,
    max_steps: int = 200,
) -> Iterator[Dict[str, Any]]:
    state: GraphState = dict(initial_state)
    adjacency = _build_adjacency(edges)
    current = entry_node_id or _pick_start_node(nodes, edges)
    step_count = 0

    yield {"event": "workflow_start", "timestamp": _timestamp(), "entryNodeId": current}

    while current and step_count < max_steps:
        step_count += 1
        base = _base_node_id(current)
        handler = NODE_REGISTRY.get(base)

        if handler is None:
            yield {
                "event": "node_skipped",
                "timestamp": _timestamp(),
                "nodeId": current,
                "log": {"level": "DEBUG", "message": f"Skipped unknown node: {current}"},
            }
        else:
            yield {
                "event": "node_start",
                "timestamp": _timestamp(),
                "nodeId": current,
                "log": {"level": "INFO", "message": f"Executing {base}"},
            }
            state = handler(state)
            yield {
                "event": "node_done",
                "timestamp": _timestamp(),
                "nodeId": current,
                "log": {"level": "SUCCESS", "message": f"Completed {base}"},
                "output": _trim_output(_extract_node_output(base, state)),
                "state": {
                    "action": state.get("action"),
                    "duplicate_found": state.get("duplicate_found"),
                    "result": state.get("result"),
                },
            }

        outgoing = adjacency.get(current, [])
        next_edge = _choose_next_edge(current, outgoing, state)
        current = str(next_edge.get("target")) if next_edge else None

    if step_count >= max_steps:
        yield {
            "event": "workflow_error",
            "timestamp": _timestamp(),
            "message": f"Execution exceeded max_steps={max_steps}",
        }

    yield {
        "event": "workflow_complete",
        "timestamp": _timestamp(),
        "result": state.get("result", {"action": "no_action", "reason": "empty_result"}),
    }

