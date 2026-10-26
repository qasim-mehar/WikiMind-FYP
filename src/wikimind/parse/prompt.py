"""The only sanctioned path for channel text to enter an LLM prompt (D4)."""

from __future__ import annotations

from collections.abc import Iterable

from .models import Channel

TRUNCATION_MARKER = "[truncated]"
_CLOSE_TAG = "</untrusted>"


def _escape(text: str) -> str:
    """Escape XML-sensitive characters so channel text cannot break the tag."""

    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _open_tag(channel: Channel) -> str:
    return f'<untrusted source="channel:{channel.channel_id}" kind="{channel.kind.value}">'


def _default_caps(
    escaped_lengths: list[int],
    overheads: list[int],
    char_budget: int,
) -> list[int] | None:
    """Water-fill the text budget across channels, or None for greedy fallback."""

    total_overhead = sum(overheads) + len(escaped_lengths) - 1
    if char_budget < total_overhead:
        return None

    text_budget = char_budget - total_overhead
    if sum(escaped_lengths) <= text_budget:
        return escaped_lengths

    nonempty = [index for index, length in enumerate(escaped_lengths) if length]
    if text_budget < len(nonempty) * len(TRUNCATION_MARKER):
        return None

    caps = [0] * len(escaped_lengths)
    active = set(nonempty)
    remaining = text_budget
    while active:
        share = remaining // len(active)
        fixed = [index for index in active if escaped_lengths[index] <= share]
        if not fixed:
            for index in active:
                caps[index] = share
            remainder = remaining - share * len(active)
            for index in sorted(active)[:remainder]:
                caps[index] += 1
            break
        for index in fixed:
            caps[index] = escaped_lengths[index]
            remaining -= escaped_lengths[index]
            active.remove(index)
    return caps


def channels_for_prompt(
    channels: Iterable[Channel],
    char_budget: int,
    *,
    per_channel_budget: int | None = None,
) -> str:
    """Render channels as tagged untrusted blocks within a total budget.

    ``char_budget`` is a hard cap on the length of the returned string,
    including tags, separators, and truncation markers.  Channel text is
    XML-escaped before it is counted or truncated, so the cap is exact.

    Each channel's text is capped at ``per_channel_budget`` when supplied.
    Otherwise the text space left after all wrapper tags and newline separators
    is water-filled: short channels keep their text and unused space flows to
    longer channels.  If that space cannot give every non-empty channel the
    truncation marker, the first channels use the remaining total budget
    instead.  If text exceeds its cap, the marker ``[truncated]`` is appended
    inside the tag.

    Channels are emitted in input order, separated by a newline.  If the next
    channel cannot fit (its wrapper plus a valid truncated payload), it and
    every later channel are omitted.
    """

    if char_budget < 0:
        raise ValueError("char_budget must be non-negative")
    if per_channel_budget is not None and per_channel_budget < 0:
        raise ValueError("per_channel_budget must be non-negative")

    items = list(channels)
    if not items or char_budget == 0:
        return ""

    overheads = [len(_open_tag(c)) + len(_CLOSE_TAG) for c in items]
    escaped_texts = [_escape(channel.text) for channel in items]
    escaped_lengths = [len(text) for text in escaped_texts]

    if per_channel_budget is not None:
        caps = [per_channel_budget] * len(items)
    else:
        caps = _default_caps(escaped_lengths, overheads, char_budget)
        if caps is None:
            caps = [char_budget] * len(items)

    parts: list[str] = []
    used = 0
    for channel, overhead, cap, escaped in zip(items, overheads, caps, escaped_texts):
        separator = 1 if parts else 0
        remaining = char_budget - used - separator
        if remaining < overhead:
            break
        effective_cap = min(cap, remaining - overhead)

        if len(escaped) <= effective_cap:
            body = escaped
        else:
            if effective_cap < len(TRUNCATION_MARKER):
                break
            body = escaped[: effective_cap - len(TRUNCATION_MARKER)] + TRUNCATION_MARKER

        block = _open_tag(channel) + body + _CLOSE_TAG
        parts.append(block)
        used += separator + len(block)

    return "\n".join(parts)
