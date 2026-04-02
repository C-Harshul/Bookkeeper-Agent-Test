import json
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


def parse_structured_output(
    *,
    parser: PydanticOutputParser,
    system_prompt: str,
    user_payload: Dict[str, Any],
) -> Any:
    llm = make_llm()
    response = llm.invoke(
        [
            SystemMessage(content=system_prompt),
            HumanMessage(content=json.dumps(user_payload, default=str)),
        ]
    )
    return parser.parse(response.content)

