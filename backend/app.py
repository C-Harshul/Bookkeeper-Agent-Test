from typing import Any, Dict

from backend.graph import build_graph
from backend.utils import log_step


def run_once(email_payload: Dict[str, Any]) -> Dict[str, Any]:
    log_step("run_once", "Starting workflow execution")
    app = build_graph()
    output = app.invoke({"email": email_payload})
    result = output.get("result", {"action": "no_action", "reason": "empty_result"})
    log_step("run_once", f"Workflow finished with action={result.get('action')}")
    return result

