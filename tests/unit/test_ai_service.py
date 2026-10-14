"""Tests for server-side AI service."""

from pathlib import Path
from typing import Any

import pytest

from executive_copilot.config import AIConfig, AppConfig, ModelRoutingConfig
from executive_copilot.credentials import CredentialManager
from executive_copilot.exceptions import ConfigurationError
from executive_copilot.services.ai import AIMessage, AIService, AITextRequest


class FakeContainer:
    """Minimal service container for AI service tests."""

    def __init__(self, config: AppConfig) -> None:
        self.config = config


class FakeUsage:
    """Fake SDK usage object."""

    def model_dump(self) -> dict[str, int]:
        return {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15}


class FakeResponse:
    """Fake Responses API response."""

    id = "resp_test"
    model = "gpt-5.4-mini"
    output_text = "A concise summary."
    usage = FakeUsage()


class FakeResponses:
    """Fake OpenAI responses resource."""

    def __init__(self) -> None:
        self.last_request: dict[str, Any] | None = None

    async def create(self, **kwargs: Any) -> FakeResponse:
        self.last_request = kwargs
        return FakeResponse()


class FakeOpenAIClient:
    """Fake OpenAI client."""

    def __init__(self) -> None:
        self.responses = FakeResponses()


def test_legacy_model_routing_migrates_to_ai_config() -> None:
    config = AppConfig(
        model_routing=ModelRoutingConfig(
            default_model="gpt-5.4",
            fallback_model="gpt-5.4-nano",
            max_tokens=2048,
            temperature=0.3,
        ),
    )

    assert config.ai.default_model == "gpt-5.4"
    assert config.ai.fallback_model == "gpt-5.4-nano"
    assert config.ai.max_output_tokens == 2048
    assert config.ai.temperature == 0.3


def test_explicit_ai_config_wins_over_legacy_model_routing() -> None:
    config = AppConfig(
        ai=AIConfig(default_model="gpt-5.4-mini"),
        model_routing=ModelRoutingConfig(default_model="gpt-5.4"),
    )

    assert config.ai.default_model == "gpt-5.4-mini"


def test_choose_model_uses_task_tiers() -> None:
    config = AppConfig(
        ai=AIConfig(
            default_model="gpt-5.4-mini",
            premium_model="gpt-5.5",
            fallback_model="gpt-5.4-nano",
        ),
    )
    service = AIService(FakeContainer(config))

    assert service.choose_model() == "gpt-5.4-mini"
    assert service.choose_model(premium=True) == "gpt-5.5"
    assert service.choose_model(fallback=True) == "gpt-5.4-nano"


def test_openai_api_key_prefers_ui_credential_store(
    tmp_path: Path,
) -> None:
    credential_manager = CredentialManager(tmp_path / ".credentials")
    credential_manager.save_credential(
        provider_type="ai",
        provider_name="openai",
        api_key="sk-from-ui",
    )
    service = AIService(FakeContainer(AppConfig(config_dir=tmp_path)))

    assert service._get_openai_api_key() == "sk-from-ui"


@pytest.mark.asyncio
async def test_generate_text_uses_responses_api_settings() -> None:
    config = AppConfig(
        ai=AIConfig(
            default_model="gpt-5.4-mini",
            reasoning_effort="high",
            text_verbosity="low",
            max_output_tokens=1024,
            temperature=0.1,
            store=False,
        ),
    )
    client = FakeOpenAIClient()
    service = AIService(FakeContainer(config), client=client)

    result = await service.generate_text(
        AITextRequest(
            input=[AIMessage(role="user", content="Summarize this thread.")],
            instructions="Return an executive-ready summary.",
            metadata={"task": "summary"},
        )
    )

    assert result.text == "A concise summary."
    assert result.model == "gpt-5.4-mini"
    assert result.response_id == "resp_test"
    assert result.usage["total_tokens"] == 15

    request = client.responses.last_request
    assert request is not None
    assert request["model"] == "gpt-5.4-mini"
    assert request["input"] == [{"role": "user", "content": "Summarize this thread."}]
    assert request["instructions"] == "Return an executive-ready summary."
    assert request["max_output_tokens"] == 1024
    assert request["temperature"] == 0.1
    assert request["reasoning"] == {"effort": "high"}
    assert request["text"] == {"verbosity": "low"}
    assert request["store"] is False
    assert request["metadata"] == {"task": "summary"}


@pytest.mark.asyncio
async def test_generate_text_requires_ui_credential_without_client(tmp_path: Path) -> None:
    config = AppConfig(config_dir=tmp_path)
    service = AIService(FakeContainer(config))

    with pytest.raises(ConfigurationError):
        await service.generate_text(AITextRequest(input="Hello"))
