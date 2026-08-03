"""Server-side AI service."""
from typing import Any, Literal, NoReturn

from openai import APIStatusError, AsyncOpenAI
from openai import RateLimitError as OpenAIRateLimitError
from pydantic import BaseModel, Field

from executive_copilot.admin.credentials import CredentialManager
from executive_copilot.config import AIProvider
from executive_copilot.exceptions import ConfigurationError, ExternalServiceError, RateLimitError
from executive_copilot.services.base import Service


class AIMessage(BaseModel):
    """A model input message."""

    role: Literal["developer", "system", "user", "assistant"] = Field(description="Message role")
    content: str = Field(description="Message content")


class AITextRequest(BaseModel):
    """Request for model-generated text."""

    input: str | list[AIMessage] = Field(description="Prompt text or message list")
    instructions: str | None = Field(default=None, description="System/developer instructions")
    model: str | None = Field(default=None, description="Explicit model override")
    premium: bool = Field(default=False, description="Use the high-capability model")
    max_output_tokens: int | None = Field(default=None, ge=1, description="Output token limit")
    temperature: float | None = Field(default=None, ge=0, le=2, description="Sampling temperature")
    metadata: dict[str, str] = Field(default_factory=dict, description="Provider request metadata")


class AITextResult(BaseModel):
    """Model text response with provider metadata."""

    text: str = Field(description="Generated text")
    model: str = Field(description="Model used")
    response_id: str | None = Field(default=None, description="Provider response ID")
    usage: dict[str, Any] = Field(default_factory=dict, description="Token usage metadata")


class AIService(Service):
    """AI orchestration service.

    This service is for Executive Copilot's own background intelligence, such as
    summarization, classification, and extraction. MCP clients still choose the
    assistant model used to call the server tools.
    """

    def __init__(self, container: Any, client: Any | None = None) -> None:
        super().__init__(container)
        self._client = client

    def choose_model(self, *, premium: bool = False, fallback: bool = False) -> str:
        """Choose the configured model for a task tier."""
        config = self.container.config.ai
        if fallback and config.fallback_model:
            return config.fallback_model
        if premium:
            return config.premium_model
        return config.default_model

    async def generate_text(self, request: AITextRequest) -> AITextResult:
        """Generate text using the configured AI provider."""
        config = self.container.config.ai
        if config.provider != AIProvider.OPENAI:
            raise ConfigurationError(
                "Unsupported AI provider",
                config_key="ai.provider",
                details={"provider": config.provider},
            )

        model = request.model or self.choose_model(premium=request.premium)
        client = self._get_openai_client()
        responses_input = self._format_input(request.input)

        create_kwargs: dict[str, Any] = {
            "model": model,
            "input": responses_input,
            "instructions": request.instructions,
            "max_output_tokens": request.max_output_tokens or config.max_output_tokens,
            "temperature": (
                request.temperature if request.temperature is not None else config.temperature
            ),
            "store": config.store,
            "metadata": request.metadata or None,
        }
        # reasoning + text.verbosity are only accepted by GPT-5 / o-series
        # reasoning models. Sending them to gpt-4o* returns a 400, so gate them.
        if self._supports_reasoning(model):
            create_kwargs["reasoning"] = {"effort": config.reasoning_effort.value}
            create_kwargs["text"] = {"verbosity": config.text_verbosity.value}

        try:
            response = await client.responses.create(**create_kwargs)
        except Exception as exc:
            self._raise_provider_error(exc)

        return AITextResult(
            text=self._extract_output_text(response),
            model=getattr(response, "model", model),
            response_id=getattr(response, "id", None),
            usage=self._dump_usage(getattr(response, "usage", None)),
        )

    @staticmethod
    def _supports_reasoning(model: str) -> bool:
        """Whether a model accepts the Responses `reasoning`/`text.verbosity` params."""
        m = model.lower()
        return m.startswith(("gpt-5", "o1", "o3", "o4"))

    def _get_openai_client(self) -> Any:
        """Create the OpenAI async client lazily."""
        if self._client is not None:
            return self._client

        self._client = AsyncOpenAI(
            api_key=self._get_openai_api_key(),
            timeout=self.container.config.ai.timeout_seconds,
        )
        return self._client

    def _get_openai_api_key(self) -> str:
        """Get OpenAI API key from credentials saved through the UI."""
        config = self.container.config.ai
        credential = self._get_stored_openai_credential()
        if credential:
            api_key = credential.get("api_key")
            if isinstance(api_key, str) and api_key:
                return api_key

        raise ConfigurationError(
            "OpenAI API key is not configured",
            config_key="credentials.ai.openai.api_key",
            details={
                "credential_provider_type": config.credential_provider_type,
                "credential_provider_name": config.credential_provider_name,
            },
        )

    def _get_stored_openai_credential(self) -> dict[str, Any] | None:
        """Read OpenAI credentials saved through the existing configuration UI."""
        config = self.container.config
        credentials_dir = config.config_dir / ".credentials"
        if not credentials_dir.exists():
            return None

        manager = CredentialManager(credentials_dir)
        provider_name = config.ai.effective_credential_name
        return manager.get_credential(
            config.ai.credential_provider_type,
            provider_name,
        )

    def _format_input(self, value: str | list[AIMessage]) -> str | list[dict[str, str]]:
        """Format internal messages for the Responses API."""
        if isinstance(value, str):
            return value
        return [message.model_dump() for message in value]

    def _extract_output_text(self, response: Any) -> str:
        """Extract text from a Responses API response."""
        output_text = getattr(response, "output_text", None)
        if isinstance(output_text, str):
            return output_text

        chunks: list[str] = []
        for output in getattr(response, "output", []) or []:
            for content in getattr(output, "content", []) or []:
                text = getattr(content, "text", None)
                if isinstance(text, str):
                    chunks.append(text)
        return "\n".join(chunks)

    def _dump_usage(self, usage: Any) -> dict[str, Any]:
        """Convert SDK usage metadata to a plain dictionary."""
        if usage is None:
            return {}
        if hasattr(usage, "model_dump"):
            dumped = usage.model_dump()
            return dumped if isinstance(dumped, dict) else {}
        if isinstance(usage, dict):
            return usage
        return {}

    def _raise_provider_error(self, exc: Exception) -> NoReturn:
        """Translate OpenAI SDK errors into application exceptions."""
        if isinstance(exc, OpenAIRateLimitError):
            raise RateLimitError(provider_name="openai", cause=exc) from exc

        if isinstance(exc, APIStatusError):
            raise ExternalServiceError(
                "OpenAI request failed",
                provider_name="openai",
                status_code=exc.status_code,
                response_body=str(exc.response),
                cause=exc,
            ) from exc

        raise ExternalServiceError(
            "OpenAI request failed",
            provider_name="openai",
            cause=exc,
        ) from exc
