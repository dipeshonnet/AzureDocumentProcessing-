from __future__ import annotations

from typing import Any

from app.config import AppSettings


def create_azure_openai_chat_client(settings: AppSettings) -> Any:
    """Create a chat-completions client for both Azure OpenAI API styles."""

    endpoint = (settings.azure_openai_endpoint or "").strip()
    api_key = settings.azure_openai_api_key
    api_version = (settings.azure_openai_api_version or "").strip()
    if not endpoint or not api_key or not api_version:
        raise ValueError("Azure OpenAI endpoint, API key, and API version are required.")

    if is_v1_endpoint(endpoint=endpoint, api_version=api_version):
        from openai import OpenAI

        return OpenAI(
            api_key=api_key,
            base_url=v1_base_url(endpoint),
        )

    from openai import AzureOpenAI

    return AzureOpenAI(
        azure_endpoint=legacy_azure_endpoint(endpoint),
        api_key=api_key,
        api_version=api_version,
    )


def chat_completion_kwargs(
    *,
    model: str,
    messages: list[dict[str, str]],
    response_format: dict,
    settings: AppSettings,
) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "response_format": response_format,
    }
    if not is_reasoning_model(model):
        kwargs["temperature"] = 0
    return kwargs


def is_v1_endpoint(*, endpoint: str, api_version: str) -> bool:
    return api_version.lower() == "v1" or endpoint.rstrip("/").lower().endswith("/openai/v1")


def v1_base_url(endpoint: str) -> str:
    normalized = endpoint.rstrip("/")
    if normalized.lower().endswith("/openai/v1"):
        return normalized
    return f"{normalized}/openai/v1"


def legacy_azure_endpoint(endpoint: str) -> str:
    normalized = endpoint.rstrip("/")
    suffix = "/openai/v1"
    if normalized.lower().endswith(suffix):
        return normalized[: -len(suffix)]
    return normalized


def is_reasoning_model(model: str) -> bool:
    normalized = model.lower()
    return normalized.startswith(("gpt-5", "o1", "o3", "o4"))
