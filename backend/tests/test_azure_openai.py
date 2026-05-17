from __future__ import annotations

import types

from app.config import AppSettings
from app.services.azure_openai import (
    chat_completion_kwargs,
    create_azure_openai_chat_client,
    legacy_azure_endpoint,
    v1_base_url,
)


class FakeOpenAI:
    kwargs: dict | None = None

    def __init__(self, **kwargs) -> None:
        FakeOpenAI.kwargs = kwargs


class FakeAzureOpenAI:
    kwargs: dict | None = None

    def __init__(self, **kwargs) -> None:
        FakeAzureOpenAI.kwargs = kwargs


def openai_settings(endpoint: str, api_version: str) -> AppSettings:
    return AppSettings.from_mapping(
        {
            "AZURE_OPENAI_ENDPOINT": endpoint,
            "AZURE_OPENAI_API_KEY": "secret",
            "AZURE_OPENAI_API_VERSION": api_version,
        }
    )


def test_v1_client_uses_openai_base_url(monkeypatch) -> None:
    fake_module = types.SimpleNamespace(OpenAI=FakeOpenAI, AzureOpenAI=FakeAzureOpenAI)
    monkeypatch.setitem(__import__("sys").modules, "openai", fake_module)

    create_azure_openai_chat_client(
        openai_settings(
            "https://admission-document-resource.services.ai.azure.com/openai/v1",
            "v1",
        )
    )

    assert FakeOpenAI.kwargs == {
        "api_key": "secret",
        "base_url": "https://admission-document-resource.services.ai.azure.com/openai/v1",
    }


def test_legacy_client_uses_azure_endpoint_and_api_version(monkeypatch) -> None:
    fake_module = types.SimpleNamespace(OpenAI=FakeOpenAI, AzureOpenAI=FakeAzureOpenAI)
    monkeypatch.setitem(__import__("sys").modules, "openai", fake_module)

    create_azure_openai_chat_client(
        openai_settings(
            "https://admission-document-resource.openai.azure.com/",
            "2024-10-21",
        )
    )

    assert FakeAzureOpenAI.kwargs == {
        "azure_endpoint": "https://admission-document-resource.openai.azure.com",
        "api_key": "secret",
        "api_version": "2024-10-21",
    }


def test_endpoint_normalization_helpers() -> None:
    assert (
        v1_base_url("https://resource.openai.azure.com")
        == "https://resource.openai.azure.com/openai/v1"
    )
    assert (
        legacy_azure_endpoint("https://resource.openai.azure.com/openai/v1")
        == "https://resource.openai.azure.com"
    )


def test_reasoning_model_request_omits_temperature() -> None:
    kwargs = chat_completion_kwargs(
        model="gpt-5-mini",
        messages=[{"role": "user", "content": "Return JSON."}],
        response_format={"type": "json_object"},
        settings=openai_settings("https://resource.openai.azure.com/openai/v1", "v1"),
    )

    assert "temperature" not in kwargs


def test_non_reasoning_model_request_uses_zero_temperature() -> None:
    kwargs = chat_completion_kwargs(
        model="gpt-4.1-mini",
        messages=[{"role": "user", "content": "Return JSON."}],
        response_format={"type": "json_object"},
        settings=openai_settings("https://resource.openai.azure.com", "2024-10-21"),
    )

    assert kwargs["temperature"] == 0
