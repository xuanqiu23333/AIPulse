"""OpenAI-compatible DeepSeek Chat Completions with bounded calls and safe errors."""

import json
from dataclasses import dataclass
from time import monotonic
from typing import Generic, Literal, Protocol, TypeVar

import httpx
from openai import (
    APIConnectionError, APIResponseValidationError, APIStatusError,
    APITimeoutError, AsyncOpenAI, OpenAIError,
)
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from aipulse.llm.config import DeepSeekSettings
from aipulse.llm.validation import SchemaDiagnostic, schema_diagnostic


class LLMError(RuntimeError):
    """A stable, non-sensitive category suitable for logging and callers."""

    def __init__(self, code: str, *, usage: "TokenUsage | None" = None,
                 elapsed_seconds: float | None = None, diagnostic: SchemaDiagnostic | None = None):
        self.code = code
        self.usage = usage
        self.elapsed_seconds = elapsed_seconds
        self.diagnostic = diagnostic
        super().__init__(f"LLM call failed: {code}")


class GenerationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True,
                              hide_input_in_errors=True)

    system_prompt: str = Field(min_length=1)
    user_prompt: str = Field(min_length=1)
    max_tokens: int = Field(default=2048, ge=1, le=8192)
    use_research_model: bool = False
    reasoning_effort: Literal["high", "max"] | None = None


class TokenUsage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    prompt_tokens: int = Field(ge=0)
    completion_tokens: int = Field(ge=0)
    total_tokens: int = Field(ge=0)
    reasoning_tokens: int | None = Field(default=None, ge=0)


class GenerationResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    text: str
    model: str
    usage: TokenUsage | None
    elapsed_seconds: float = Field(ge=0)


def _response_usage(response) -> TokenUsage | None:
    """Keep valid billing counters even when the answer itself is unusable."""
    try:
        usage = response.usage
        if usage is None:
            return None
        details = getattr(usage, "completion_tokens_details", None)
        return TokenUsage(
            prompt_tokens=usage.prompt_tokens,
            completion_tokens=usage.completion_tokens,
            total_tokens=usage.total_tokens,
            reasoning_tokens=getattr(details, "reasoning_tokens", None),
        )
    except (AttributeError, TypeError, ValidationError, ValueError):
        return None  # Missing/malformed counters are unknown, never zero-filled.


T = TypeVar("T", bound=BaseModel)


@dataclass(frozen=True, slots=True)
class StructuredGenerationResult(Generic[T]):
    value: T
    model: str
    usage: TokenUsage | None
    elapsed_seconds: float


class LLMGateway(Protocol):
    async def generate_text(self, request: GenerationRequest) -> GenerationResult: ...

    async def generate_json(
        self, request: GenerationRequest, schema: type[T], *, example: T,
    ) -> StructuredGenerationResult[T]: ...

    async def aclose(self) -> None: ...


class DeepSeekGateway:
    """One provider; replace this class to use another compatible model."""

    def __init__(
        self, settings: DeepSeekSettings, *,
        http_client: httpx.AsyncClient | None = None, max_retries: int = 2,
    ) -> None:
        if not 0 <= max_retries <= 2:
            raise ValueError("max_retries must be between 0 and 2")
        self._settings = settings
        self._client = AsyncOpenAI(
            api_key=settings.api_key.get_secret_value(),
            base_url=settings.base_url,
            timeout=settings.timeout_seconds,
            max_retries=max_retries,
            http_client=http_client,
        )

    async def __aenter__(self) -> "DeepSeekGateway":
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.close()

    async def generate_text(self, request: GenerationRequest) -> GenerationResult:
        return await self._complete(request)

    async def generate_json(
        self, request: GenerationRequest, schema: type[T], *, example: T,
    ) -> StructuredGenerationResult[T]:
        if not isinstance(example, schema):
            raise LLMError("invalid_request")
        instruction = (
            "Return ONLY a JSON object matching the JSON SCHEMA. Do not include Markdown. "
            "Respect required fields, types, patterns, nested objects and length limits. "
            "Do not add fields. For arrays that allow empty values use []; omit only non-required fields. "
            "Use null only where the schema explicitly permits it.\n"
            f"JSON SCHEMA:\n{json.dumps(schema.model_json_schema(), ensure_ascii=False, separators=(',', ':'))}\n"
            f"EXAMPLE JSON OUTPUT:\n{example.model_dump_json()}"
        )
        result = await self._complete(request, json_instruction=instruction)
        try:
            parsed = json.loads(result.text)
        except json.JSONDecodeError:
            raise LLMError("invalid_json", usage=result.usage,
                           elapsed_seconds=result.elapsed_seconds) from None
        try:
            value = schema.model_validate(parsed, strict=True)
        except ValidationError as error:
            raise LLMError("schema_mismatch", usage=result.usage,
                           elapsed_seconds=result.elapsed_seconds,
                           diagnostic=schema_diagnostic(error, schema, 'provider_output')) from None
        return StructuredGenerationResult(
            value=value, model=result.model, usage=result.usage,
            elapsed_seconds=result.elapsed_seconds,
        )

    async def _complete(
        self, request: GenerationRequest, *, json_instruction: str | None = None,
    ) -> GenerationResult:
        model = (self._settings.research_model if request.use_research_model
                 else self._settings.model)
        effort = request.reasoning_effort or (
            self._settings.research_reasoning_effort if request.use_research_model
            else self._settings.reasoning_effort)
        system_prompt = request.system_prompt
        if json_instruction is not None:
            system_prompt = f"{system_prompt}\n{json_instruction}"
        options = {"response_format": {"type": "json_object"}} if json_instruction else {}
        started = monotonic()
        try:
            response = await self._client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": request.user_prompt},
                ],
                reasoning_effort=effort,
                extra_body={"thinking": {"type": "enabled"}},
                max_tokens=request.max_tokens,
                stream=False,
                **options,
            )
        except APITimeoutError:
            raise LLMError("timeout") from None
        except APIConnectionError:
            raise LLMError("connection_error") from None
        except APIStatusError as error:
            raise LLMError(_http_error_code(error.status_code)) from None
        except APIResponseValidationError:
            raise LLMError("invalid_response") from None
        except OpenAIError:
            raise LLMError("provider_error") from None

        token_usage = _response_usage(response)
        elapsed_seconds = monotonic() - started

        def response_error(code):
            return LLMError(code, usage=token_usage, elapsed_seconds=elapsed_seconds)

        try:
            choice = response.choices[0]
            if choice.finish_reason == "length":
                raise response_error("output_truncated")
            if choice.finish_reason != "stop":
                raise response_error("invalid_response")
            content = choice.message.content
            if not isinstance(content, str) or not content.strip():
                raise response_error("empty_response")
            return GenerationResult(
                text=content, model=response.model, usage=token_usage,
                elapsed_seconds=elapsed_seconds,
            )
        except LLMError:
            raise
        except (AttributeError, IndexError, TypeError, ValidationError, ValueError):
            raise response_error("invalid_response") from None


def _http_error_code(status: int) -> str:
    if status == 401:
        return "authentication_failed"
    if status == 402:
        return "insufficient_balance"
    if status == 429:
        return "rate_limited"
    if status >= 500:
        return "service_unavailable"
    if status in (400, 422):
        return "invalid_request"
    return "request_rejected"
