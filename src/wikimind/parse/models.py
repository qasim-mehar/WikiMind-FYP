"""Pydantic contract for parsed documents and channels."""

from __future__ import annotations

from enum import Enum

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from .ids import channel_id, document_sha256, normalize_text

_BUILTIN_PARSER_NAMES = frozenset({"text", "pdf-fallback", "grobid"})


class ChannelKind(str, Enum):
    """The closed set of channel kinds."""

    TITLE = "title"
    ABSTRACT = "abstract"
    SECTION = "section"
    TABLE = "table"
    FIGURE_CAPTION = "figure_caption"
    REFERENCE = "reference"


class Channel(BaseModel):
    """One typed, addressable passage of a parsed document.

    ``normalized`` and ``channel_id`` are derived.  They may be supplied when
    reading JSON, but they must match the derivation or validation fails.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: ChannelKind
    ordinal: int = Field(ge=0)
    text: str
    normalized: str | None = None
    channel_id: str | None = None
    page: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def _derive_and_verify_ids(self) -> Channel:
        if "normalized" in self.model_fields_set and self.normalized is None:
            raise ValueError("normalized must be a string when supplied")
        if "channel_id" in self.model_fields_set and self.channel_id is None:
            raise ValueError("channel_id must be a string when supplied")

        expected_normalized = normalize_text(self.text)
        expected_id = channel_id(self.kind.value, expected_normalized)
        if self.normalized is not None and self.normalized != expected_normalized:
            raise ValueError("normalized text does not match the normalization contract")
        if self.channel_id is not None and self.channel_id != expected_id:
            raise ValueError("channel_id does not match the ID contract")
        object.__setattr__(self, "normalized", expected_normalized)
        object.__setattr__(self, "channel_id", expected_id)
        return self

    @property
    def source_ref(self) -> str:
        """Return the prompt-facing reference, e.g. ``channel:<sha256>``."""

        return f"channel:{self.channel_id}"


class ParsedDocument(BaseModel):
    """Deterministic parse output for exactly one source file."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: int = Field(default=1, ge=1)
    parser: str = Field(min_length=1)
    source_filename: str = Field(min_length=1)
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_bytes: int = Field(ge=0)
    page_count: int | None = Field(default=None, ge=0)
    channels: tuple[Channel, ...]

    @field_validator("schema_version")
    @classmethod
    def _supported_schema_version(cls, value: int) -> int:
        if value != 1:
            raise ValueError(f"unsupported schema_version {value}; expected 1")
        return value

    @field_validator("parser")
    @classmethod
    def _registered_parser(cls, value: str) -> str:
        if value in _BUILTIN_PARSER_NAMES:
            return value
        from .registry import parser_names

        if value not in parser_names():
            raise ValueError(f"unknown parser {value!r}")
        return value

    @field_validator("source_filename")
    @classmethod
    def _basename_only(cls, value: str) -> str:
        if "/" in value or "\\" in value:
            raise ValueError("source_filename must be a basename")
        return value

    @model_validator(mode="after")
    def _verify_document(self) -> ParsedDocument:
        counters: dict[ChannelKind, int] = {}
        for channel in self.channels:
            expected = counters.get(channel.kind, 0)
            if channel.ordinal != expected:
                raise ValueError(
                    f"channel ordinal for {channel.kind.value!r} must be "
                    f"{expected}, got {channel.ordinal}"
                )
            counters[channel.kind] = expected + 1

        if self.parser == "pdf-fallback":
            if self.page_count is None or self.page_count < 1:
                raise ValueError("pdf-fallback requires page_count >= 1")
            for channel in self.channels:
                if channel.page is None:
                    raise ValueError("pdf-fallback channels require page")
                if channel.page > self.page_count:
                    raise ValueError(
                        f"channel page {channel.page} exceeds page_count {self.page_count}"
                    )
        else:
            if self.page_count is not None:
                raise ValueError(f"{self.parser} requires page_count=None")
            for channel in self.channels:
                if channel.page is not None:
                    raise ValueError(f"{self.parser} channels require page=None")
        return self

    def channels_of_kind(self, kind: ChannelKind | str) -> tuple[Channel, ...]:
        """Return channels of one kind in document order."""

        wanted = ChannelKind(kind)
        return tuple(c for c in self.channels if c.kind is wanted)


class ChannelBuilder:
    """Assign per-kind ordinals in emission order."""

    def __init__(self) -> None:
        self._channels: list[Channel] = []
        self._counts: dict[ChannelKind, int] = {}

    def add(self, kind: ChannelKind | str, text: str, page: int | None = None) -> Channel:
        resolved = ChannelKind(kind)
        ordinal = self._counts.get(resolved, 0)
        channel = Channel(
            kind=resolved,
            ordinal=ordinal,
            text=text,
            page=page,
        )
        self._channels.append(channel)
        self._counts[resolved] = ordinal + 1
        return channel

    def finish(self) -> tuple[Channel, ...]:
        return tuple(self._channels)

    def __len__(self) -> int:
        return len(self._channels)


def _basename(source_filename: str) -> str:
    name = source_filename.replace("\\", "/").rsplit("/", 1)[-1].strip()
    return name or "document"


def make_document(
    parser: str,
    source_filename: str,
    data: bytes,
    channels: tuple[Channel, ...] | list[Channel],
    page_count: int | None = None,
) -> ParsedDocument:
    """Build a ParsedDocument, computing the raw-byte SHA-256."""

    return ParsedDocument(
        parser=parser,
        source_filename=_basename(source_filename),
        source_sha256=document_sha256(data),
        source_bytes=len(data),
        page_count=page_count,
        channels=tuple(channels),
    )
