import json
import os
import re
from typing import Any, Dict

from langchain_community.chat_models import ChatOllama
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.output_parsers import PydanticOutputParser

from backend.config import get_env

try:
    from langchain_google_genai import ChatGoogleGenerativeAI
except ImportError:
    ChatGoogleGenerativeAI = None


def make_llm() -> BaseChatModel:
    provider = get_env("LLM_PROVIDER", "ollama").strip().lower()
    temperature = float(get_env("LLM_TEMPERATURE", "0"))

    if provider == "gemini":
        if ChatGoogleGenerativeAI is None:
            raise ImportError(
                "langchain-google-genai is not installed. Install dependencies from requirements.txt."
            )
        model = get_env("GEMINI_MODEL", "gemini-2.5-flash")
        api_key = get_env("GOOGLE_API_KEY")
        return ChatGoogleGenerativeAI(model=model, temperature=temperature, google_api_key=api_key)

    model = get_env("OLLAMA_MODEL", "qwen2.5:7b")
    base_url = get_env("OLLAMA_BASE_URL", "")
    kwargs: Dict[str, Any] = {"model": model, "temperature": temperature}
    if base_url:
        kwargs["base_url"] = base_url
    return ChatOllama(**kwargs)


def make_ollama_chat_json() -> ChatOllama:
    """Ollama with JSON mode — much more reliable for Pydantic parsing than free-form text."""
    temperature = float(get_env("LLM_TEMPERATURE", "0"))
    model = get_env("OLLAMA_MODEL", "qwen2.5:7b")
    base_url = get_env("OLLAMA_BASE_URL", "")
    kwargs: Dict[str, Any] = {"model": model, "temperature": temperature, "format": "json"}
    if base_url.strip():
        kwargs["base_url"] = base_url.strip()
    return ChatOllama(**kwargs)


def _stringify_ai_content(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and "text" in block:
                parts.append(str(block["text"]))
            else:
                parts.append(str(block))
        return "".join(parts)
    return str(content)


def _extract_json_object_text(text: str) -> str:
    text = (text or "").strip()
    if not text:
        return text
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```\s*$", "", text)
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        return text[start : end + 1]
    return text


def _payload_json_size(payload: Dict[str, Any]) -> int:
    return len(json.dumps(payload, default=str))


def _maybe_shrink_payload_for_ollama(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Trim only as much as needed. Prefer keeping ALL items and ALL vendors; shrink accounts first."""
    max_chars = int(os.getenv("LLM_MAX_PAYLOAD_CHARS", "48000"))
    if _payload_json_size(payload) <= max_chars:
        return payload

    data = json.loads(json.dumps(payload, default=str))
    ref = data.get("reference")
    if not isinstance(ref, dict):
        return data

    items_full = list(ref["items"]) if isinstance(ref.get("items"), list) else []
    vendors_full = list(ref["vendors"]) if isinstance(ref.get("vendors"), list) else []
    accounts_full = list(ref["accounts"]) if isinstance(ref.get("accounts"), list) else []
    customers_full = list(ref["customers"]) if isinstance(ref.get("customers"), list) else []

    def apply_and_check(account_cap: int | None, item_cap: int | None, vendor_cap: int | None) -> bool:
        ref["items"] = items_full if item_cap is None else items_full[:item_cap]
        ref["vendors"] = vendors_full if vendor_cap is None else vendors_full[:vendor_cap]
        ref["accounts"] = accounts_full if account_cap is None else accounts_full[:account_cap]
        if customers_full:
            ref["customers"] = customers_full
        ref.pop("accounts_total_in_company", None)
        ref.pop("items_total_in_company", None)
        ref.pop("vendors_total_in_company", None)
        ref.pop("_reference_truncated", None)
        ref.pop("_accounts_truncated", None)
        ref.pop("_items_truncated", None)
        ref.pop("_vendors_truncated", None)
        if account_cap is not None and account_cap < len(accounts_full):
            ref["accounts_total_in_company"] = len(accounts_full)
            ref["_accounts_truncated"] = True
        if item_cap is not None and item_cap < len(items_full):
            ref["items_total_in_company"] = len(items_full)
            ref["_items_truncated"] = True
        if vendor_cap is not None and vendor_cap < len(vendors_full):
            ref["vendors_total_in_company"] = len(vendors_full)
            ref["_vendors_truncated"] = True
        if ref.get("_accounts_truncated") or ref.get("_items_truncated") or ref.get("_vendors_truncated"):
            ref["_reference_truncated"] = True
        return _payload_json_size(data) <= max_chars

    # 1) Full items + vendors; only reduce accounts (skip if bill reference has no accounts — e.g. invoice payload)
    if accounts_full:
        account_caps_seen: set[int] = set()
        for ac_raw in (
            10**9,
            80,
            60,
            45,
            32,
            24,
            16,
            12,
            8,
        ):
            acap = min(ac_raw, len(accounts_full))
            if acap in account_caps_seen:
                continue
            account_caps_seen.add(acap)
            if apply_and_check(acap, None, None):
                return data

    # 2) Still too large — trim items (vendors kept full; accounts stay tight)
    item_caps_seen: set[int] = set()
    for ic_raw in (40, 28, 20, 14, 10):
        icap = min(ic_raw, len(items_full)) if items_full else 0
        if icap in item_caps_seen:
            continue
        item_caps_seen.add(icap)
        acap = min(12, len(accounts_full)) if accounts_full else None
        if apply_and_check(acap, icap if items_full else None, None):
            return data

    # 3) Last resort — trim vendors
    vendor_caps_seen: set[int] = set()
    for v_raw in (50, 35, 25, 18):
        vcap = min(v_raw, len(vendors_full)) if vendors_full else 0
        if vcap in vendor_caps_seen:
            continue
        vendor_caps_seen.add(vcap)
        icap = min(10, len(items_full)) if items_full else None
        acap = min(8, len(accounts_full)) if accounts_full else None
        if apply_and_check(acap, icap, vcap):
            return data

    return data


_STRICT_JSON_SUFFIX = (
    "\n\nOutput rules: Return ONLY a single JSON object that matches the schema. "
    "Do not analyze, summarize, or interpret the reference lists (no commentary on balances or net worth). "
    "No markdown code fences, no text before or after the JSON."
)


def parse_structured_output(
    *,
    parser: PydanticOutputParser,
    system_prompt: str,
    user_payload: Dict[str, Any],
) -> Any:
    llm = make_llm()
    system_content = system_prompt + _STRICT_JSON_SUFFIX
    messages = [
        SystemMessage(content=system_content),
        HumanMessage(content=json.dumps(user_payload, default=str)),
    ]

    # Gemini: native structured output avoids prose and OUTPUT_PARSING_FAILURE from chatty models.
    if ChatGoogleGenerativeAI is not None and isinstance(llm, ChatGoogleGenerativeAI):
        model_cls = parser.pydantic_object
        structured = llm.with_structured_output(model_cls)
        result = structured.invoke(messages)
        if isinstance(result, model_cls):
            return result
        if isinstance(result, dict):
            return model_cls.model_validate(result)
        return result

    if isinstance(llm, ChatOllama):
        llm_json = make_ollama_chat_json()
        payload = _maybe_shrink_payload_for_ollama(dict(user_payload))
        messages = [
            SystemMessage(content=system_content),
            HumanMessage(content=json.dumps(payload, default=str)),
        ]
        response = llm_json.invoke(messages)
        text = _extract_json_object_text(_stringify_ai_content(response.content))
        model_cls = parser.pydantic_object
        try:
            return model_cls.model_validate_json(text)
        except Exception:
            return parser.parse(text)

    response = llm.invoke(messages)
    text = _extract_json_object_text(_stringify_ai_content(response.content))
    try:
        return parser.pydantic_object.model_validate_json(text)
    except Exception:
        return parser.parse(text)

