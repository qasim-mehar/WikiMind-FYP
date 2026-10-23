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
    return (
        f'<untrusted source="channel:{channel.channel_id}" '
        f'kind="{channel.kind.value}">'
    )


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
    Otherwise it gets an equal share of the space left after all wrapper tags
    and newline separators.  If text exceeds its cap, the marker
    ``[truncated]`` is appended inside the tag.

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
    separator_count = len(items) - 1
    available = char_budget - sum(overheads) - separator_count
    if available > 0:
        default_cap = available // len(items)
    else:
        default_cap = char_budget
    cap = per_channel_budget if per_channel_budget is not None else default_cap

    parts: list[str] = []
    used = 0
    for channel, overhead in zip(items, overheads):
        separator = 1 if parts else 0
        remaining = char_budget - used - separator
        if remaining < overhead:
            break
        effective_cap = min(cap, remaining - overhead)

        escaped = _escape(channel.text)
        if len(escaped) <= effective_cap:
            body = escaped
        else:
            if effective_cap < len(TRUNCATION_MARKER):
                break
            body = (
                escaped[: effective_cap - len(TRUNCATION_MARKER)]
                + TRUNCATION_MARKER
            )

        block = _open_tag(channel) + body + _CLOSE_TAG
        parts.append(block)
        used += separator + len(block)

    return "\n".join(parts)
