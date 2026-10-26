"""Validated inputs and outcomes shared by future news collectors."""

import json
from datetime import datetime, timezone
from enum import Enum
from typing import Annotated, Any

from pydantic import (
    AnyHttpUrl,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    computed_field,
    field_validator,
    model_validator,
)


Provider = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        to_lower=True,
        min_length=1,
        max_length=16,
        pattern=r"^[a-z][a-z0-9_]*$",
    ),
]
ExternalId = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=191)]
Title = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=512)]


class CollectorSource(BaseModel):
    """A source configuration identity, independent of any ORM model."""

    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    source_id: int = Field(gt=0)
    provider: Provider
    external_id: ExternalId


class NewsCandidate(BaseModel):
    """One bounded original item returned by a provider adapter."""

    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    provider: Provider
    external_id: ExternalId
    original_url: AnyHttpUrl
    title: Title
    raw_excerpt: str = Field(max_length=4000)
    published_at: datetime
    fetched_at: datetime
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("original_url")
    @classmethod
    def require_safe_http_url(cls, value: AnyHttpUrl) -> AnyHttpUrl:
        if value.username or value.password or len(str(value)) > 2048:
            raise ValueError("Invalid news URL")
        return value

    @field_validator("published_at", "fetched_at")
    @classmethod
    def require_aware_utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Timestamp must include timezone")
        return value.astimezone(timezone.utc)

    @field_validator("metadata")
    @classmethod
    def require_bounded_json(cls, value: dict[str, Any]) -> dict[str, Any]:
        try:
            encoded = json.dumps(value, allow_nan=False, ensure_ascii=False)
        except (TypeError, ValueError, OverflowError, RecursionError):
            raise ValueError("Metadata must be JSON serializable") from None
        if len(encoded.encode("utf-8")) > 16_384:
            raise ValueError("Metadata exceeds size limit")
        return value


class CollectionStatus(str, Enum):
    """Outcome of one bounded collection call."""

    SUCCESS = "success"
    PARTIAL = "partial"
    FAILED = "failed"


class CollectorErrorCode(str, Enum):
    """Stable error classes suitable for persistence and reporting."""

    TIMEOUT = "timeout"
    RATE_LIMIT = "rate_limit"
    INVALID_RESPONSE = "invalid_response"
    UNAVAILABLE = "unavailable"
    UNEXPECTED = "unexpected"


_SAFE_SUMMARIES = {
    CollectorErrorCode.TIMEOUT: "Source request timed out",
    CollectorErrorCode.RATE_LIMIT: "Source rate limit reached",
    CollectorErrorCode.INVALID_RESPONSE: "Source response was invalid",
    CollectorErrorCode.UNAVAILABLE: "Source is unavailable",
    CollectorErrorCode.UNEXPECTED: "Source collection failed",
}


class CollectorError(Exception):
    """Carry only a recognized code and fixed safe text, never raw errors."""

    def __init__(self, code: CollectorErrorCode):
        try:
            self.code = CollectorErrorCode(code)
        except ValueError:
            raise ValueError("Unknown collector error code") from None
        super().__init__(_SAFE_SUMMARIES[self.code])


class CollectionResult(BaseModel):
    """A page result with accepted candidates and explicit failure state.

    fetched_count counts source records seen in this call, including filtered
    records, so it may exceed the number of accepted candidates.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    source_id: int = Field(gt=0)
    candidates: tuple[NewsCandidate, ...] = Field(default_factory=tuple, max_length=1000)
    status: CollectionStatus
    fetched_count: int = Field(ge=0)
    next_cursor: str | None = Field(default=None, max_length=512)
    error_code: CollectorErrorCode | None = None

    @model_validator(mode="after")
    def validate_outcome(self) -> "CollectionResult":
        if self.fetched_count < len(self.candidates):
            raise ValueError("Fetched count is below accepted candidate count")
        if self.status is CollectionStatus.SUCCESS:
            if self.error_code is not None:
                raise ValueError("Successful collection cannot contain an error")
        elif self.status is CollectionStatus.PARTIAL:
            if not self.candidates or self.error_code is None:
                raise ValueError("Partial collection requires candidates and an error code")
        elif self.candidates or self.error_code is None:
            raise ValueError("Failed collection requires no candidates and an error code")
        return self

    @computed_field(return_type=str | None)
    @property
    def error_summary(self) -> str | None:
        """Expose a fixed summary without accepting external error text."""
        return _SAFE_SUMMARIES.get(self.error_code)
